"""Post-deploy smoke test tests for Phase 6 module 6.7.

Nothing here reaches the network. The transport is injected and every response
is built to look like one a deployed service returns, headers and all, so the
checks run against realistic traffic rather than against a mock of themselves.

The clock is injected too. Ninety seconds is what Section 7 specifies and what
production waits, and a test that waited it out would take ninety seconds to
prove one branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import smoke
from prodpilot.filechecks import SECURITY_HEADERS
from prodpilot.smoke import (
    BACKOFF,
    CORS,
    WINDOW,
    Answer,
    Check,
    Smoke,
    api,
    cors,
    headers,
    run,
    traces,
    waits,
)

# A response from a service that is set up correctly, headers and all.
GOOD = {
    "Content-Type": "application/json",
    "Content-Security-Policy": "default-src 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Access-Control-Allow-Origin": "https://app.example.com",
}

URL = "https://api-abc.onrender.com"

# A real Node stack trace, of the shape an unhandled error returns.
NODE_TRACE = (
    "TypeError: Cannot read properties of undefined (reading 'id')\n"
    "    at Object.<anonymous> (/app/src/routes/invoiceRoutes.js:12:19)\n"
    "    at Module._compile (node:internal/modules/cjs/loader:1105:14)\n"
)
PY_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "/app/main.py", line 4, in <module>\n'
    "    raise ValueError('boom')\n"
)


def clocked():
    """A clock a test moves rather than waits on."""
    ticks = [0.0]
    return ticks, (lambda: ticks[0]), (lambda s: ticks.__setitem__(0, ticks[0] + s))


def serving(health: Answer, root: Answer | None = None):
    """A service that answers /health and /api with what it is given.

    When only a health response is given, /api answers with the same headers,
    because middleware applies to every response and a service that stripped
    them from one route only would be a different scenario.
    """
    def fetch(url: str) -> Answer:
        if url.endswith("/health"):
            return health
        if root is not None:
            return root
        return Answer(200, health.headers, "{}")
    return fetch


def healthy(body: str = '{"status":"ok"}') -> Answer:
    return Answer(200, GOOD, body)


# --------------------------------------------------------------------------
# the five checks Section 7 names
# --------------------------------------------------------------------------


def test_section_seven_names_five_checks_and_all_five_run():
    """The brief said four. The document says five, including CORS."""
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(healthy()), sleep=sleep, clock=now)

    assert len(result.checks) == 5
    assert {c.name for c in result.checks} == set(Check)


def test_a_correct_deployment_is_confirmed():
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(healthy()), sleep=sleep, clock=now)

    assert result.confirmed is True
    assert result.failed == ()
    assert "verified" in result.report()


def test_any_single_failure_means_unverified():
    """Section 7: any fail produces a failure report, not partial success."""
    missing = {k: v for k, v in GOOD.items() if k != "Referrer-Policy"}
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(Answer(200, missing, "ok")), sleep=sleep, clock=now)

    assert result.confirmed is False
    assert len(result.failed) == 1
    assert result.get(Check.HEALTH).ok is True


# --------------------------------------------------------------------------
# the adaptive health probe
# --------------------------------------------------------------------------


def test_the_window_is_the_documented_ninety_seconds():
    assert WINDOW == 90.0


def test_the_backoff_grows_rather_than_being_a_fixed_wait():
    """Section 7 asks for backoff, to absorb a free tier cold start."""
    schedule = waits(WINDOW)

    assert schedule[0] < schedule[1] < schedule[2]
    assert sum(schedule) <= WINDOW
    assert list(BACKOFF) == sorted(BACKOFF)


def test_a_cold_start_is_absorbed():
    """Two 502s then healthy, which is what a sleeping free tier service does."""
    state = [0]

    def waking(url: str) -> Answer:
        if url.endswith("/health"):
            state[0] += 1
            if state[0] < 3:
                return Answer(502, {}, "Bad Gateway")
            return healthy()
        return Answer(200, GOOD, "{}")

    ticks, now, sleep = clocked()
    result = run(URL, fetch=waking, sleep=sleep, clock=now)

    assert result.confirmed is True
    assert result.attempts == 3
    assert ticks[0] == BACKOFF[0] + BACKOFF[1]


def test_a_service_that_never_answers_fails_the_probe():
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(Answer(503, {}, "")), sleep=sleep, clock=now)

    assert result.confirmed is False
    assert result.get(Check.HEALTH).ok is False
    assert "90s" in result.get(Check.HEALTH).detail


def test_a_connection_error_is_a_failed_probe_not_an_exception():
    def refused(url: str) -> Answer:
        raise ConnectionError("connection refused")

    ticks, now, sleep = clocked()
    result = run(URL, fetch=refused, sleep=sleep, clock=now)

    assert result.confirmed is False
    assert "ConnectionError" in result.get(Check.HEALTH).detail


def test_the_probe_stays_inside_its_window():
    ticks, now, sleep = clocked()

    run(URL, fetch=serving(Answer(503, {}, "")), sleep=sleep, clock=now)

    assert ticks[0] <= WINDOW


def test_nothing_else_is_judged_when_the_service_never_answered():
    """Saying a header is missing when the service is down would mislead."""
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(Answer(503, {}, "")), sleep=sleep, clock=now)

    assert len(result.failed) == 5
    for check in result.checks:
        if check.name is not Check.HEALTH:
            assert "never became healthy" in check.detail


# --------------------------------------------------------------------------
# the api response
# --------------------------------------------------------------------------


def test_a_missing_api_route_fails():
    ticks, now, sleep = clocked()
    fetch = serving(healthy(), Answer(404, GOOD, "Not Found"))

    result = run(URL, fetch=fetch, sleep=sleep, clock=now)

    assert result.get(Check.API).ok is False
    assert "404" in result.get(Check.API).detail


def test_an_erroring_api_fails():
    ticks, now, sleep = clocked()
    fetch = serving(healthy(), Answer(500, GOOD, "{}"))

    result = run(URL, fetch=fetch, sleep=sleep, clock=now)

    assert result.get(Check.API).ok is False
    assert "500" in result.get(Check.API).detail


@pytest.mark.parametrize("status", [200, 201, 204, 301, 401, 403])
def test_anything_that_is_neither_404_nor_500_passes(status: int):
    """Section 7 asks only that it is neither, so 401 is a served route."""
    check, _ = api(serving(healthy(), Answer(status, GOOD, "{}")), URL)

    assert check.ok is True


def test_an_api_that_cannot_be_reached_fails():
    def half(url: str) -> Answer:
        if url.endswith("/health"):
            return healthy()
        raise ConnectionError("reset")

    check, answer = api(half, URL)

    assert check.ok is False
    assert answer is None


# --------------------------------------------------------------------------
# security headers, on live traffic
# --------------------------------------------------------------------------


def test_the_header_names_are_the_audit_engine_s_own():
    """So the source check and the live check cannot disagree."""
    source = Path(smoke.__file__).read_text(encoding="utf-8")

    assert "from prodpilot.filechecks import SECURITY_HEADERS" in source
    assert smoke.SECURITY_HEADERS is SECURITY_HEADERS


def test_all_the_security_headers_present_passes():
    assert headers([Answer(200, GOOD, "")]).ok is True


def test_a_missing_content_security_policy_fails():
    """The header SEC-006 exists to guarantee."""
    without = {k: v for k, v in GOOD.items() if k != "Content-Security-Policy"}

    check = headers([Answer(200, without, "")])

    assert check.ok is False
    assert "Content Security Policy" in check.detail


@pytest.mark.parametrize("name", list(SECURITY_HEADERS))
def test_every_security_header_is_actually_required(name: str):
    without = {k: v for k, v in GOOD.items() if k.lower() != name}

    assert headers([Answer(200, without, "")]).ok is False


def test_a_header_stripped_in_transit_is_caught():
    """The case source analysis cannot see: the app sets it, a proxy removes it."""
    stripped = {"Content-Type": "application/json"}
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(Answer(200, stripped, "ok")), sleep=sleep, clock=now)

    assert result.get(Check.HEADERS).ok is False
    assert result.confirmed is False


def test_every_response_has_to_carry_the_headers():
    """helmet runs before any route, so one route missing them is a real gap."""
    bare = {"Content-Type": "application/json"}

    assert headers([Answer(200, GOOD, ""), Answer(200, bare, "")]).ok is False
    assert headers([Answer(200, GOOD, ""), Answer(200, GOOD, "")]).ok is True


def test_every_response_has_to_carry_the_cors_header():
    bare = {"Content-Type": "application/json"}

    assert cors([Answer(200, GOOD, ""), Answer(200, bare, "")]).ok is False


def test_header_matching_ignores_case():
    lowered = {k.lower(): v for k, v in GOOD.items()}

    assert headers([Answer(200, lowered, "")]).ok is True


# --------------------------------------------------------------------------
# CORS, the fifth check
# --------------------------------------------------------------------------


def test_a_cors_header_present_passes():
    check = cors([Answer(200, GOOD, "")])

    assert check.ok is True
    assert "https://app.example.com" in check.detail


def test_a_missing_cors_header_fails():
    without = {k: v for k, v in GOOD.items() if k != "Access-Control-Allow-Origin"}

    check = cors([Answer(200, without, "")])

    assert check.ok is False
    assert CORS in check.detail


def test_cors_is_a_separate_check_from_the_security_headers():
    """Section 7 names them separately, so a failure names the right one."""
    without = {k: v for k, v in GOOD.items() if k != "Access-Control-Allow-Origin"}
    ticks, now, sleep = clocked()

    result = run(URL, fetch=serving(Answer(200, without, "ok")), sleep=sleep, clock=now)

    assert result.get(Check.CORS).ok is False
    assert result.get(Check.HEADERS).ok is True


# --------------------------------------------------------------------------
# stack traces
# --------------------------------------------------------------------------


def test_a_node_stack_trace_in_a_body_is_caught():
    check = traces([Answer(500, GOOD, NODE_TRACE)])

    assert check.ok is False
    assert "leaks a stack trace" in check.detail


def test_a_python_traceback_in_a_body_is_caught():
    assert traces([Answer(500, GOOD, PY_TRACE)]).ok is False


def test_a_trace_in_any_response_fails_not_just_the_first():
    """Section 7 says no stack traces in any response body."""
    check = traces([Answer(200, GOOD, "ok"), Answer(500, GOOD, NODE_TRACE)])

    assert check.ok is False


@pytest.mark.parametrize(
    "body",
    ['{"status":"ok"}', "", "Not Found",
     '{"error":"invoice not found","code":404}',
     "<html><body>Service starting at 12:00</body></html>",
     '{"message":"TypeError is not allowed as a name"}'],
)
def test_an_ordinary_body_is_not_mistaken_for_a_trace(body: str):
    """A false positive here fails a good deployment, so the pattern is narrow."""
    assert traces([Answer(200, GOOD, body)]).ok is True


def test_a_leaking_deployment_is_not_confirmed():
    ticks, now, sleep = clocked()
    fetch = serving(healthy(), Answer(500, GOOD, NODE_TRACE))

    result = run(URL, fetch=fetch, sleep=sleep, clock=now)

    assert result.confirmed is False
    assert result.get(Check.TRACES).ok is False
    assert result.get(Check.API).ok is False


# --------------------------------------------------------------------------
# the detailed failure report
# --------------------------------------------------------------------------


def test_the_report_names_which_checks_failed_and_why():
    ticks, now, sleep = clocked()
    bare = {"Content-Type": "application/json"}
    fetch = serving(healthy(), Answer(500, bare, NODE_TRACE))

    result = run(URL, fetch=fetch, sleep=sleep, clock=now)
    report = result.report()

    assert "not verified" in report
    for check in result.failed:
        assert check.name.value in report
        assert check.detail.strip()


def test_the_result_serialises_whole():
    ticks, now, sleep = clocked()

    payload = run(URL, fetch=serving(healthy()), sleep=sleep, clock=now).to_dict()

    assert set(payload) == {"url", "confirmed", "attempts", "waited", "checks", "failed"}
    assert len(payload["checks"]) == 5
    json.dumps(payload)


def test_asking_for_a_check_that_does_not_exist_is_refused():
    made = Smoke(URL)

    with pytest.raises(KeyError):
        made.get(Check.HEALTH)


def test_an_empty_smoke_is_not_confirmed():
    assert Smoke(URL).confirmed is False


def test_the_api_route_can_be_one_the_project_actually_serves():
    """A versioned project answers /api with 404 by design, so the caller names its route."""
    sent = {name: "set" for name in SECURITY_HEADERS}
    sent["access-control-allow-origin"] = "*"
    asked: list[str] = []

    def fetch(url: str) -> smoke.Answer:
        asked.append(url)
        if url.endswith("/api"):
            return smoke.Answer(404, dict(sent), "")
        return smoke.Answer(200, dict(sent), '{"status":"ok"}')

    default = smoke.run("https://x.test", fetch=fetch, sleep=lambda s: None,
                        clock=lambda: 0.0)
    versioned = smoke.run("https://x.test", fetch=fetch, sleep=lambda s: None,
                          clock=lambda: 0.0, endpoint="/api/v1")

    assert not default.confirmed
    assert versioned.confirmed, versioned.report()
    assert asked[-1] == "https://x.test/api/v1"
