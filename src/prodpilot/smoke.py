"""The post-deploy smoke test, the seventh stage of ProdPush.

Scope is Phase 6 module 6.7. Section 7 stage 7 states it: an adaptive health
probe retrying GET /health with backoff for up to 90 seconds to absorb free tier
cold starts, then GET /api expecting neither 404 nor 500, security headers
present, CORS headers present, and no stack traces in any response body.

This reads a live service. It changes nothing, deploys nothing, and wires
nothing. Stage 8 is the pipeline.

Five checks, not four
---------------------
Section 7 names five things: the health probe, the api response, security
headers, CORS headers, and stack traces. All five are here. CORS is easy to
overlook because it reads as part of the header sentence, but it is a separate
condition with a separate rule behind it in the audit engine, so it gets its own
check and its own reason.

Why this is the live proof of what the audit already checked
-------------------------------------------------------------
Module 2.2's SEC-002 proves helmet is registered before the first route, and
SEC-006 proves the serving layer sets a Content Security Policy. Both read
source. This stage asks the deployed service the same question over real
traffic, which is the only place the answer can be wrong despite the source
being right, because a proxy or a platform can strip a header the application
sets.

The header names are the audit engine's own SECURITY_HEADERS, imported rather
than restated, so the audit and the smoke test cannot disagree about what a
security header is.

Both header checks require every response to carry them, not merely one. helmet
and the CORS middleware are registered before any route, which is exactly what
SEC-002 proves about the source, so they apply to everything the service
returns. A service answering /health with the headers and /api without them has
a real gap, and accepting one response as proof would hide it.

Why any single failure means unverified
----------------------------------------
Section 7 says any fail produces a detailed failure report. A deployment that
answers /health but leaks a stack trace is not partly deployed, it is a
deployment that should not be trusted, so confirmed is all five and nothing
less. The report names which of the five failed and why, so a developer does not
have to re-run anything to find out.

Why the clock is injected
--------------------------
Ninety seconds is what Section 7 specifies and what runs in production. A test
that waited it out would take ninety seconds to prove one branch, so the sleep
and the clock are arguments, exactly as module 6.6 does.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from prodpilot.filechecks import SECURITY_HEADERS

logger = logging.getLogger(__name__)

HEALTH = "/health"
API = "/api"

# Section 7 stage 7, verbatim: up to 90 seconds of retrying.
WINDOW = 90.0

# Growing waits between attempts, capped so a cold start is absorbed without
# hammering a service that is still booting.
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)

# The header that says a browser may call this service from another origin.
# Module 2.2's SEC-003 checks the source sets it, this checks it survived.
CORS = "access-control-allow-origin"

# What a leaked stack trace looks like on the runtimes this project supports.
# Kept narrow on purpose, since a false positive here fails a good deployment.
TRACES = re.compile(
    r"Traceback \(most recent call last\)"
    r"|\n\s+at [\w$.<>]+ \(.*:\d+:\d+\)"
    r"|\n\s+at /.*:\d+:\d+"
    r"|ReferenceError:|TypeError:.*\n\s+at |UnhandledPromiseRejection"
    r"|node_modules[/\\].*:\d+:\d+",
)


class Check(str, Enum):
    """The five things Section 7 stage 7 asks about."""

    HEALTH = "health probe"
    API = "api response"
    HEADERS = "security headers"
    CORS = "cors headers"
    TRACES = "stack traces"


@dataclass(frozen=True)
class Answer:
    """One HTTP response, kept small so a test can build one."""

    status: int
    headers: dict[str, str]
    body: str

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


Fetch = Callable[[str], Answer]


@dataclass(frozen=True)
class Probe:
    """One of the five checks, and what it found."""

    name: Check
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"check": self.name.value, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class Smoke:
    """The verdict on a live deployment."""

    url: str
    checks: tuple[Probe, ...] = field(default_factory=tuple)
    waited: float = 0.0
    attempts: int = 0

    @property
    def confirmed(self) -> bool:
        """All five, or the deployment is not verified."""
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failed(self) -> tuple[Probe, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def get(self, name: Check) -> Probe:
        for check in self.checks:
            if check.name is name:
                return check
        raise KeyError(f"no check named {name.value}")

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "confirmed": self.confirmed,
            "attempts": self.attempts,
            "waited": round(self.waited, 1),
            "checks": [c.to_dict() for c in self.checks],
            "failed": [c.name.value for c in self.failed],
        }

    def report(self) -> str:
        """The detailed failure report Section 7 asks for."""
        if self.confirmed:
            return f"{self.url}: deployment verified, all {len(self.checks)} checks passed"
        lines = [f"{self.url}: deployment not verified, "
                 f"{len(self.failed)} of {len(self.checks)} checks failed"]
        for check in self.failed:
            lines.append(f"  {check.name.value}: {check.detail}")
        return "\n".join(lines)


def send(url: str) -> Answer:
    """The default transport. One GET, no retries, no interpretation."""
    request = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "prodpilot-smoke"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return Answer(reply.status, dict(reply.headers),
                          reply.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return Answer(exc.code, dict(exc.headers or {}),
                      exc.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as exc:
        raise ConnectionError(str(getattr(exc, "reason", exc))) from exc


def waits(window: float) -> list[float]:
    """The backoff schedule, growing and then holding, within the window."""
    out: list[float] = []
    spent = 0.0
    step = 0
    while True:
        pause = BACKOFF[min(step, len(BACKOFF) - 1)]
        if spent + pause > window:
            return out
        out.append(pause)
        spent += pause
        step += 1


def probe(fetch: Fetch, base: str, window: float = WINDOW,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic,
          path: str = HEALTH) -> tuple[Probe, Answer | None, int, float]:
    """Retry the health path with backoff until it answers or the window closes.

    A cold free tier service can take most of a minute to wake, so a single
    request would fail a deployment that is merely asleep.

    path is /health for a service that runs, which is what Section 7 specifies
    and what module 2.2's OBS-001 requires of an Express project. A built front
    end has no such route and serves its application at the root instead, so a
    caller deploying one passes that root. The default is unchanged.
    """
    url = base.rstrip("/") + path
    started = clock()
    schedule = waits(window)
    attempts = 0
    last = "no response"

    for index in range(len(schedule) + 1):
        try:
            answer = fetch(url)
            attempts += 1
            if 200 <= answer.status < 300:
                return (Probe(Check.HEALTH, True,
                              f"answered {answer.status} after {attempts} attempt(s)"),
                        answer, attempts, clock() - started)
            last = f"status {answer.status}"
        except Exception as exc:
            attempts += 1
            last = f"{type(exc).__name__}: {exc}"
        if index < len(schedule):
            sleep(schedule[index])

    return (Probe(Check.HEALTH, False,
                  f"no healthy response within {window:.0f}s, last was {last}"),
            None, attempts, clock() - started)


def api(fetch: Fetch, base: str) -> tuple[Probe, Answer | None]:
    """GET /api, which must answer with neither 404 nor 500."""
    url = base.rstrip("/") + API
    try:
        answer = fetch(url)
    except Exception as exc:
        return Probe(Check.API, False, f"{API} could not be reached: {exc}"), None

    if answer.status == 404:
        return Probe(Check.API, False, f"{API} returned 404, the route is not served"), answer
    if answer.status >= 500:
        return Probe(Check.API, False,
                     f"{API} returned {answer.status}, the service errored"), answer
    return Probe(Check.API, True, f"{API} returned {answer.status}"), answer


def headers(answers: list[Answer]) -> Probe:
    """The security headers module 2.2 proves the source sets.

    Checked on live traffic because a proxy can strip what an application sets,
    which source analysis cannot see.
    """
    if not answers:
        return Probe(Check.HEADERS, False, "no response was available to inspect")

    for answer in answers:
        present = {k.lower() for k in answer.headers if k.lower() in SECURITY_HEADERS}
        missing = sorted(set(SECURITY_HEADERS) - present)
        if "content-security-policy" in missing:
            return Probe(Check.HEADERS, False,
                         f"no Content Security Policy on a live response, missing "
                         f"{', '.join(missing)}")
        if missing:
            return Probe(Check.HEADERS, False,
                         f"{len(missing)} security header(s) missing from a live "
                         f"response: {', '.join(missing)}")
    return Probe(Check.HEADERS, True,
                 f"all {len(SECURITY_HEADERS)} security headers on every response")


def cors(answers: list[Answer]) -> Probe:
    """The CORS header Section 7 names alongside the security headers."""
    if not answers:
        return Probe(Check.CORS, False, "no response was available to inspect")
    for answer in answers:
        if not answer.header(CORS):
            return Probe(Check.CORS, False, f"no {CORS} header on a live response")
    return Probe(Check.CORS, True,
                 f"{CORS} is {answers[0].header(CORS)} on every response")


def traces(answers: list[Answer]) -> Probe:
    """No response body may carry a stack trace."""
    for answer in answers:
        found = TRACES.search(answer.body or "")
        if found:
            return Probe(Check.TRACES, False,
                         f"a response body leaks a stack trace: "
                         f"{found.group(0).strip()[:80]}")
    return Probe(Check.TRACES, True, f"no stack trace in {len(answers)} response body(ies)")


def run(url: str, fetch: Fetch = send, window: float = WINDOW,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        health: str = HEALTH) -> Smoke:
    """Run all five checks against a live deployment.

    Never raises. A service that cannot be reached is a failed health check, not
    an exception, so a caller always gets a report it can show a developer.
    """
    health, first, attempts, waited = probe(fetch, url, window, sleep,
                                            clock, health)
    answers: list[Answer] = [a for a in (first,) if a is not None]

    if not health.ok:
        # Nothing else can be judged if the service never answered, and saying
        # a header is missing when the service is down would be misleading.
        checks = (
            health,
            Probe(Check.API, False, "not checked, the service never became healthy"),
            Probe(Check.HEADERS, False, "not checked, the service never became healthy"),
            Probe(Check.CORS, False, "not checked, the service never became healthy"),
            Probe(Check.TRACES, False, "not checked, the service never became healthy"),
        )
        result = Smoke(url, checks, waited, attempts)
        logger.warning("%s", result.report())
        return result

    root, answer = api(fetch, url)
    if answer is not None:
        answers.append(answer)

    result = Smoke(url, (health, root, headers(answers), cors(answers),
                         traces(answers)), waited, attempts)
    logger.info("%s", result.report())
    return result
