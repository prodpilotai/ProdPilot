"""GitHub client tests for Phase 5 module 5.1.

Everything here runs offline. The transport and the clock are injected, so the
paths that matter most, waiting for a rate limit reset and backing off on a
secondary limit, are exercised without the network and without real delays.

What is being checked is that the client paces itself from what the API reports
rather than from a number written into the source, because that is the only
version that stays correct for both token and no-token use.
"""

from __future__ import annotations

import json

import pytest

from prodpilot import gh
from prodpilot.gh import (
    BACKOFF,
    FLOOR,
    PER_PAGE,
    SEARCH_CAP,
    Client,
    GhError,
    Limits,
    NotFound,
    Response,
    Unreachable,
    read_limits,
    retry_after,
)


def reply(status: int = 200, body=None, **headers) -> Response:
    raw = json.dumps(body if body is not None else {}).encode("utf-8")
    return Response(status, headers, raw)


def quota(remaining: int, reset: int = 0, limit: int = 5000) -> dict[str, str]:
    return {
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(reset),
        "x-ratelimit-limit": str(limit),
    }


class Fake:
    """A transport that answers from a queue and records what it was asked."""

    def __init__(self, *replies: Response) -> None:
        self.replies = list(replies)
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Response:
        self.urls.append(url)
        self.headers.append(headers)
        if not self.replies:
            raise AssertionError(f"no reply queued for {url}")
        return self.replies.pop(0)


def client(*replies: Response, now: float = 1000.0):
    """A client with a fake transport and a sleep that only records."""
    slept: list[float] = []
    fake = Fake(*replies)
    made = Client(auth="t", fetch=fake, sleep=slept.append, clock=lambda: now)
    return made, fake, slept


# --------------------------------------------------------------------------
# requests and authentication
# --------------------------------------------------------------------------


def test_a_token_is_sent_as_a_bearer_header():
    made, fake, _ = client(reply(body={"ok": True}))

    made.get("/rate_limit")

    assert fake.headers[0]["Authorization"] == "Bearer t"
    assert fake.headers[0]["X-GitHub-Api-Version"] == gh.VERSION


def test_no_token_means_no_authorization_header():
    fake = Fake(reply(body={}))
    made = Client(auth=None, fetch=fake, sleep=lambda s: None)

    made.get("/rate_limit")

    assert "Authorization" not in fake.headers[0]
    assert made.named is False


def test_parameters_are_encoded_onto_the_url():
    made, fake, _ = client(reply(body={}))

    made.get("/search/repositories", {"q": "express stars:1..2", "page": 2})

    assert "q=express+stars%3A1..2" in fake.urls[0]
    assert "page=2" in fake.urls[0]


def test_a_missing_path_raises_not_found():
    made, _, _ = client(reply(404, {"message": "Not Found"}))

    with pytest.raises(NotFound):
        made.get("/repos/nobody/nothing")


def test_an_unexpected_status_carries_the_message_github_gave():
    made, _, _ = client(reply(422, {"message": "Validation Failed"}))

    with pytest.raises(GhError) as caught:
        made.get("/search/repositories")

    assert "Validation Failed" in str(caught.value)


def test_a_body_that_is_not_json_is_refused():
    made, _, _ = client(Response(200, {}, b"<html>not json</html>"))

    with pytest.raises(GhError):
        made.get("/rate_limit")


# --------------------------------------------------------------------------
# rate limits, read from the response rather than assumed
# --------------------------------------------------------------------------


def test_the_quota_headers_are_read_off_every_response():
    made, _, _ = client(reply(body={}, **quota(4321, 1700, 5000)))

    made.get("/rate_limit")

    assert made.limits == Limits(remaining=4321, reset=1700, limit=5000)


def test_headers_that_are_absent_keep_the_previous_reading():
    previous = Limits(remaining=10, reset=50, limit=60)

    assert read_limits({}, previous) == previous


def test_a_header_that_is_not_a_number_is_ignored():
    previous = Limits(remaining=10, reset=50, limit=60)

    assert read_limits({"x-ratelimit-remaining": "soon"}, previous).remaining == 10


def test_the_client_waits_for_the_reset_when_the_quota_is_nearly_spent():
    """The point of reading the headers: stop before the limit, not after."""
    made, _, slept = client(
        reply(body={}, **quota(FLOOR, reset=1060)),
        reply(body={}, **quota(4999, reset=1060)),
        now=1000.0,
    )

    made.get("/one")
    assert slept == []

    made.get("/two")

    assert slept == [61.0]
    assert made.waited == 61.0


def test_no_wait_while_there_is_quota_left():
    made, _, slept = client(
        reply(body={}, **quota(500, reset=9999)),
        reply(body={}, **quota(499, reset=9999)),
    )

    made.get("/one")
    made.get("/two")

    assert slept == []


def test_a_reset_already_past_does_not_wait():
    limits = Limits(remaining=0, reset=900)

    assert limits.wait(now=1000.0) == 0.0


def test_an_unknown_quota_does_not_wait():
    assert Limits().wait(now=1000.0) == 0.0


# --------------------------------------------------------------------------
# secondary limits and server errors
# --------------------------------------------------------------------------


def test_a_secondary_limit_obeys_the_retry_after_header():
    """GitHub's documented instruction, and ignoring it risks a ban."""
    made, _, slept = client(
        Response(403, {"Retry-After": "45"}, b"{}"),
        reply(body={"ok": True}),
    )

    assert made.get("/thing") == {"ok": True}
    assert 45.0 in slept


def test_a_spent_quota_without_retry_after_waits_for_the_reset():
    made, _, slept = client(
        Response(429, quota(0, reset=1120), b"{}"),
        reply(body={"ok": True}),
        now=1000.0,
    )

    made.get("/thing")

    assert 121.0 in slept


def test_a_secondary_limit_with_no_guidance_backs_off():
    made, _, slept = client(
        Response(403, {}, b"{}"),
        reply(body={"ok": True}),
    )

    made.get("/thing")

    assert slept[0] == float(BACKOFF[0])


def test_a_server_error_is_retried_and_then_succeeds():
    made, fake, _ = client(
        reply(503, {"message": "unavailable"}),
        reply(body={"ok": True}),
    )

    assert made.get("/thing") == {"ok": True}
    assert len(fake.urls) == 2


def test_retries_are_bounded_and_then_give_up():
    made, _, slept = client(*[reply(503, {}) for _ in range(len(BACKOFF) + 1)])

    with pytest.raises(GhError) as caught:
        made.get("/thing")

    assert "did not succeed" in str(caught.value)
    assert slept == [float(s) for s in BACKOFF]


def test_the_backoff_grows():
    assert list(BACKOFF) == sorted(BACKOFF)
    assert len(set(BACKOFF)) == len(BACKOFF)


def test_retry_after_prefers_the_header_over_the_reset():
    waited = retry_after({"retry-after": "12"}, Limits(remaining=0, reset=9999), now=0.0)

    assert waited == 12.0


def test_a_malformed_retry_after_falls_through_to_the_reset():
    waited = retry_after({"retry-after": "soon"}, Limits(remaining=0, reset=100), now=0.0)

    assert waited == 101.0


# --------------------------------------------------------------------------
# pagination
# --------------------------------------------------------------------------


def page(count: int) -> Response:
    return reply(body={"items": [{"full_name": f"o/r{i}"} for i in range(count)]})


def test_search_pages_until_a_short_page_ends_it():
    made, fake, _ = client(page(PER_PAGE), page(PER_PAGE), page(7))

    items = list(made.search("express"))

    assert len(items) == PER_PAGE * 2 + 7
    assert len(fake.urls) == 3
    assert "page=3" in fake.urls[2]


def test_search_stops_on_an_empty_page():
    made, fake, _ = client(page(PER_PAGE), page(0))

    assert len(list(made.search("express"))) == PER_PAGE
    assert len(fake.urls) == 2


def test_search_never_asks_past_the_thousand_result_ceiling():
    """GitHub refuses beyond 1000, so asking is a wasted call and an error."""
    made, fake, _ = client(*[page(PER_PAGE) for _ in range(SEARCH_CAP // PER_PAGE)])

    items = list(made.search("express"))

    assert len(items) == SEARCH_CAP
    assert len(fake.urls) == SEARCH_CAP // PER_PAGE


def test_a_caller_can_ask_for_fewer_than_the_ceiling():
    made, fake, _ = client(page(PER_PAGE))

    items = list(made.search("express", cap=30))

    assert len(items) == 30
    assert len(fake.urls) == 1


def test_search_asks_for_the_largest_page_the_api_allows():
    made, fake, _ = client(page(3))

    list(made.search("express"))

    assert f"per_page={PER_PAGE}" in fake.urls[0]


# --------------------------------------------------------------------------
# the three repository reads collection needs
# --------------------------------------------------------------------------


def test_the_root_listing_returns_file_names():
    made, _, _ = client(reply(body=[
        {"name": "package.json", "type": "file"},
        {"name": "vite.config.js", "type": "file"},
        {"name": "src", "type": "dir"},
    ]))

    assert made.root("o/r", "main") == ["package.json", "vite.config.js", "src"]


def test_a_root_listing_that_is_not_a_list_is_refused():
    made, _, _ = client(reply(body={"message": "This is a file"}))

    with pytest.raises(GhError):
        made.root("o/r", "main")


def test_a_file_is_decoded_from_base64():
    import base64

    body = base64.b64encode(b'{"name":"thing"}').decode()
    made, _, _ = client(reply(body={"encoding": "base64", "content": body}))

    assert made.text("o/r", "package.json", "main") == '{"name":"thing"}'


def test_a_file_returned_in_another_encoding_is_refused():
    made, _, _ = client(reply(body={"encoding": "none", "content": ""}))

    with pytest.raises(GhError):
        made.text("o/r", "package.json", "main")


def test_the_head_commit_pins_the_entry():
    made, _, _ = client(reply(body={"sha": "a" * 40}))

    assert made.head("o/r", "main") == "a" * 40


def test_a_commit_response_with_no_sha_is_refused():
    made, _, _ = client(reply(body={}))

    with pytest.raises(GhError):
        made.head("o/r", "main")


# --------------------------------------------------------------------------
# the token comes from the credential store
# --------------------------------------------------------------------------


def test_the_token_is_read_through_module_1_3(monkeypatch):
    """One way credentials are loaded, and no second copy of that logic."""
    from prodpilot.config import Credentials

    monkeypatch.setattr(gh, "load_credentials", lambda: Credentials(github_token="abc"))

    assert gh.token() == "abc"


def test_a_missing_credential_store_is_not_an_error(monkeypatch):
    from prodpilot.config import Credentials

    monkeypatch.setattr(gh, "load_credentials", lambda: Credentials())

    assert gh.token() is None


def test_an_unreadable_credential_store_is_reported_as_no_token(monkeypatch):
    from prodpilot.config import ConfigError

    def boom():
        raise ConfigError("config.toml is not valid TOML")

    monkeypatch.setattr(gh, "load_credentials", boom)

    assert gh.token() is None


def test_no_token_is_written_into_the_source():
    """A hardcoded credential would be a leak in a public repository."""
    source = (gh.__file__)
    text = open(source, encoding="utf-8").read()

    assert "ghp_" not in text
    assert "github_pat_" not in text


# --------------------------------------------------------------------------
# a network that drops mid run
# --------------------------------------------------------------------------


class Flaky:
    """A transport that cannot reach GitHub for the first few attempts."""

    def __init__(self, failures: int, then: Response) -> None:
        self.failures = failures
        self.then = then
        self.tries = 0

    def __call__(self, url: str, headers: dict[str, str]) -> Response:
        self.tries += 1
        if self.tries <= self.failures:
            raise Unreachable(f"cannot reach {url}: getaddrinfo failed")
        return self.then


def test_a_dropped_connection_is_retried_rather_than_given_up_on():
    """A collection of hundreds of repositories will meet a blip."""
    slept: list[float] = []
    fetch = Flaky(2, reply(body={"ok": True}))
    made = Client(auth="t", fetch=fetch, sleep=slept.append, clock=lambda: 0.0)

    assert made.get("/thing") == {"ok": True}
    assert fetch.tries == 3
    assert slept == [float(BACKOFF[0]), float(BACKOFF[1])]


def test_a_network_that_stays_down_reports_being_unreachable():
    """The error has to stay distinguishable, so a caller can tell it apart."""
    fetch = Flaky(len(BACKOFF) + 1, reply(body={}))
    made = Client(auth="t", fetch=fetch, sleep=lambda s: None)

    with pytest.raises(Unreachable) as caught:
        made.get("/thing")

    assert "cannot reach" in str(caught.value)


def test_an_unreachable_request_is_not_counted_as_a_call():
    fetch = Flaky(2, reply(body={}))
    made = Client(auth="t", fetch=fetch, sleep=lambda s: None)

    made.get("/thing")

    assert made.calls == 1


def test_unreachable_is_a_kind_of_error_callers_can_still_catch_broadly():
    assert issubclass(Unreachable, GhError)
    assert issubclass(NotFound, GhError)
