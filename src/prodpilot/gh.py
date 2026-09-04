"""A small GitHub REST client for dataset collection.

Scope is Phase 5 module 5.1. This is the only part of ProdPilot that talks to
the GitHub REST API for research data, and it exists to do three things well:
authenticate from the credential store, stay inside the documented rate limits,
and page through results without losing its place.

No HTTP dependency is added. Section 9 of the Complete Solution Document names
the GitHub REST API but no client library, and the whole of what is needed here
is authenticated GET with headers, which urllib does. Keeping the dependency
list at three packages matters more than the small convenience a client library
would add.

The limits this respects, from GitHub's own documentation
---------------------------------------------------------
An authenticated personal access token gets 5000 core requests per hour. The
search endpoints are separate and much tighter: 30 requests per minute, and a
hard ceiling of 1000 results for any single query no matter how many pages are
asked for. Secondary limits can appear at any time and are signalled with a
retry-after header, which must be obeyed before retrying.

Rather than hardcode those numbers, the client reads x-ratelimit-remaining and
x-ratelimit-reset off every response and paces itself from what the API
actually reports. That is correct for both token and no-token use, and it stays
correct if GitHub changes a limit.

Testing without the network
---------------------------
The transport is injected. A test supplies a callable that returns a Response,
so every path here, including waiting for a reset and backing off on a
secondary limit, is exercised offline and without real sleeping.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from prodpilot.config import ConfigError, load_credentials

logger = logging.getLogger(__name__)

API = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
VERSION = "2022-11-28"
AGENT = "prodpilot-dataset"

# Search refuses to page past this, whatever per_page is set to.
SEARCH_CAP = 1000
PER_PAGE = 100

# Stop and wait for the reset while this few requests remain, so a burst near
# the boundary does not trip the limit outright.
FLOOR = 2

# Backoff for secondary limits and server errors, in seconds.
BACKOFF = (2, 8, 30, 60)


class GhError(Exception):
    """Raised when a request cannot be completed."""


class NotFound(GhError):
    """Raised when the API reports that a path does not exist."""


class Unreachable(GhError):
    """Raised when the request never reached GitHub.

    Kept apart from the other failures because it says nothing about the
    repository being asked for. A DNS blip during a long collection must be
    retried, never recorded as a verdict on a candidate.
    """


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GhError(f"the response was not valid JSON: {exc}") from exc


@dataclass(frozen=True)
class Limits:
    """What the last response said about the remaining quota."""

    remaining: int | None = None
    reset: int | None = None
    limit: int | None = None

    @property
    def known(self) -> bool:
        return self.remaining is not None

    def wait(self, now: float) -> float:
        """Seconds to wait before the next request, zero when there is quota."""
        if self.remaining is None or self.remaining > FLOOR:
            return 0.0
        if self.reset is None:
            return float(BACKOFF[0])
        return max(0.0, self.reset - now + 1)


def token() -> str | None:
    """The stored GitHub token, or None when the credential store has none.

    Read through module 1.3 so there is one way credentials are loaded and no
    second copy of that logic. A missing store is not an error here, since the
    caller decides whether to proceed without a token.
    """
    try:
        return load_credentials().github_token
    except ConfigError as exc:
        logger.warning("cannot read the credential store: %s", exc)
        return None


def send(url: str, headers: dict[str, str]) -> Response:
    """The default transport. One GET, no retries, no interpretation."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            return Response(reply.status, dict(reply.headers), reply.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers or {}), exc.read())
    except urllib.error.URLError as exc:
        raise Unreachable(f"cannot reach {url}: {exc.reason}") from exc
    except http.client.HTTPException as exc:
        # A connection that dies part way through the body. Not an OSError, so
        # it needs naming separately, and it is every bit as transient.
        raise Unreachable(f"cannot reach {url}: {type(exc).__name__}") from exc
    except OSError as exc:
        raise Unreachable(f"cannot reach {url}: {exc}") from exc


Fetch = Callable[[str, dict[str, str]], Response]


class Client:
    """Authenticated GitHub REST access that paces itself.

    fetch and sleep are injected so the retry and waiting behaviour can be
    driven in a test without the network and without real delays.
    """

    def __init__(
        self,
        auth: str | None = None,
        fetch: Fetch = send,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.auth = auth
        self.fetch = fetch
        self.sleep = sleep
        self.clock = clock
        self.limits = Limits()
        self.calls = 0
        self.waited = 0.0

    @property
    def named(self) -> bool:
        """Whether requests carry a token."""
        return bool(self.auth)

    def headers(self) -> dict[str, str]:
        out = {
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": VERSION,
            "User-Agent": AGENT,
        }
        if self.auth:
            out["Authorization"] = f"Bearer {self.auth}"
        return out

    def get(self, path: str, params: dict[str, object] | None = None):
        """One API call, waiting and retrying as the response headers direct."""
        url = path if path.startswith("http") else f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        last: Unreachable | None = None
        for attempt, pause in enumerate((0.0,) + BACKOFF):
            if pause:
                self.hold(pause, f"attempt {attempt}")
            self.hold(self.limits.wait(self.clock()), "the rate limit")

            try:
                reply = self.fetch(url, self.headers())
            except Unreachable as exc:
                # The same treatment as a server error. A collection of
                # hundreds of repositories will meet a transient network
                # failure, and giving up on the first one wastes the run.
                logger.warning("%s, retrying", exc)
                last = exc
                continue
            self.calls += 1
            self.limits = read_limits(reply.headers, self.limits)

            if reply.status == 200:
                return reply.json()
            if reply.status == 404:
                raise NotFound(f"not found: {url}")
            if reply.status in (403, 429):
                self.hold(retry_after(reply.headers, self.limits, self.clock()),
                          "a secondary rate limit")
                continue
            if reply.status >= 500:
                logger.warning("%s returned %s, retrying", url, reply.status)
                continue
            raise GhError(f"{url} returned {reply.status}: {detail(reply)}")

        if last is not None:
            raise last
        raise GhError(f"{url} did not succeed after {len(BACKOFF)} retries")

    def hold(self, seconds: float, why: str) -> None:
        if seconds <= 0:
            return
        logger.info("waiting %.0fs for %s", seconds, why)
        self.waited += seconds
        self.sleep(seconds)

    def search(self, query: str, cap: int = SEARCH_CAP) -> Iterator[dict]:
        """Repository search results, one page at a time.

        Stops at GitHub's 1000 result ceiling rather than asking for a page the
        API will refuse. A caller that needs more than that has to split the
        query, which is what the collector does with star ranges.
        """
        cap = min(cap, SEARCH_CAP)
        seen = 0
        for page in range(1, cap // PER_PAGE + 2):
            if seen >= cap:
                return
            payload = self.get("/search/repositories", {
                "q": query,
                "per_page": PER_PAGE,
                "page": page,
                "sort": "stars",
                "order": "desc",
            })
            items = payload.get("items") or []
            if not items:
                return
            for item in items:
                if seen >= cap:
                    return
                seen += 1
                yield item
            if len(items) < PER_PAGE:
                return

    def root(self, full_name: str, ref: str) -> list[str]:
        """The names of the files and directories at a repository's root."""
        listing = self.get(f"/repos/{full_name}/contents", {"ref": ref})
        if not isinstance(listing, list):
            raise GhError(f"{full_name} root listing was not a list")
        return [entry["name"] for entry in listing if isinstance(entry, dict) and "name" in entry]

    def text(self, full_name: str, path: str, ref: str) -> str:
        """One file's contents, decoded."""
        import base64

        payload = self.get(f"/repos/{full_name}/contents/{path}", {"ref": ref})
        if not isinstance(payload, dict) or payload.get("encoding") != "base64":
            raise GhError(f"{full_name}:{path} was not returned as a base64 file")
        try:
            return base64.b64decode(payload["content"]).decode("utf-8", "replace")
        except (KeyError, ValueError) as exc:
            raise GhError(f"{full_name}:{path} could not be decoded: {exc}") from exc

    def head(self, full_name: str, branch: str) -> str:
        """The commit sha at the tip of a branch, which pins the dataset entry."""
        payload = self.get(f"/repos/{full_name}/commits/{branch}")
        sha = payload.get("sha") if isinstance(payload, dict) else None
        if not isinstance(sha, str) or not sha:
            raise GhError(f"{full_name} returned no commit sha for {branch}")
        return sha


def read_limits(headers: dict[str, str], previous: Limits) -> Limits:
    """Pull the quota headers off a response, keeping what is not reported."""
    lower = {k.lower(): v for k, v in headers.items()}

    def number(key: str, fallback: int | None) -> int | None:
        raw = lower.get(key)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except ValueError:
            return fallback

    return Limits(
        remaining=number("x-ratelimit-remaining", previous.remaining),
        reset=number("x-ratelimit-reset", previous.reset),
        limit=number("x-ratelimit-limit", previous.limit),
    )


def retry_after(headers: dict[str, str], limits: Limits, now: float) -> float:
    """How long a 403 or 429 says to wait.

    GitHub's guidance is to obey retry-after when it is present, then to wait
    for the reset when the quota is spent, and otherwise to back off.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    raw = lower.get("retry-after")
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    if limits.remaining == 0 and limits.reset is not None:
        return max(0.0, limits.reset - now + 1)
    return float(BACKOFF[0])


def detail(reply: Response) -> str:
    """The message GitHub gave, for an error a person has to read."""
    try:
        payload = reply.json()
    except GhError:
        return reply.body[:200].decode("utf-8", "replace")
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return str(payload)[:200]
