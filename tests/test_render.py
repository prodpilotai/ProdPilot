"""Render provider and deploy monitor tests for Phase 6 modules 6.5 and 6.6.

Nothing here reaches the network. The transport is injected and every response
is built from the shapes Render's published API reference describes, so the
tests exercise the real parsing without an API key and without an account.

The clock is injected too. Section 7's fifteen second interval and ten minute
limit are what production uses, and a test that waited them out would take ten
minutes to prove one branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import monitor, projectstate, render
from prodpilot.buildtest import Fault
from prodpilot.monitor import EVERY, LIMIT, Outcome, Watch, watch
from prodpilot.provider import METHODS, Deployment, Provider, Service, Status
from prodpilot.render import STATES, Render, RenderError, Reply, store

KEY = "made-up-render-key"
OWNER = "tea-abc123"


def reply(status: int = 200, body=None) -> Reply:
    return Reply(status, json.dumps(body if body is not None else {}).encode())


class Api:
    """A scripted Render, answering by path and recording what it was asked."""

    def __init__(self, routes: dict[str, Reply]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, url, method, headers, body) -> Reply:
        self.calls.append((method, url, body))
        self.headers.append(headers)
        for fragment, answer in self.routes.items():
            if fragment in url:
                return answer
        raise AssertionError(f"unscripted call {method} {url}")

    def body_of(self, fragment: str) -> dict:
        for method, url, body in self.calls:
            if fragment in url and body:
                return json.loads(body.decode())
        raise AssertionError(f"no body sent to {fragment}")


OWNERS = [{"owner": {"id": OWNER, "name": "Team", "email": "t@example.com",
                     "type": "team"}, "cursor": "c"}]

CREATED = {
    "service": {"id": "srv-abc", "url": "https://api-abc.onrender.com",
                "name": "api", "type": "web_service"},
    "deployId": "dep-xyz",
}


def provider(routes: dict[str, Reply] | None = None) -> tuple[Render, Api]:
    api = Api(routes or {"/owners": reply(200, OWNERS),
                         "/services": reply(201, CREATED)})
    return Render(api_key=KEY, fetch=api), api


# --------------------------------------------------------------------------
# the interface, which module 6.1 defined first
# --------------------------------------------------------------------------


def test_render_satisfies_the_provider_interface():
    """The first real implementation, checked structurally rather than assumed."""
    made, _ = provider()

    assert isinstance(made, Provider)


def test_render_offers_every_method_the_interface_names():
    made, _ = provider()

    for name in METHODS:
        assert callable(getattr(made, name, None)), name


def test_the_interface_was_not_changed_to_fit_the_implementation():
    """6.9 asks whether a second provider could be added without a rewrite."""
    import inspect

    for name in METHODS:
        expected = list(inspect.signature(getattr(Provider, name)).parameters)
        actual = list(inspect.signature(getattr(Render, name)).parameters)
        assert actual == expected, name


def test_a_missing_api_key_is_refused(monkeypatch):
    from prodpilot import config

    monkeypatch.setattr(render, "key", lambda: None)

    with pytest.raises(RenderError):
        Render(fetch=lambda *a: reply())


def test_the_key_is_read_through_module_1_3(tmp_path: Path, monkeypatch):
    from prodpilot import config

    monkeypatch.setenv(config.CONFIG_HOME_ENV_VAR, str(tmp_path / "home"))
    config.save_credentials(config.Credentials(github_token="t", render_api_key="stored"))

    made = Render(fetch=lambda *a: reply())

    assert made.api_key == "stored"


# --------------------------------------------------------------------------
# 6.5, creating the service
# --------------------------------------------------------------------------


def test_a_service_is_created_and_the_three_identifiers_come_back():
    made, api = provider()

    result = made.deploy(Service(name="api", repo="https://github.com/o/r.git",
                                 branch="main", build="npm ci", start="npm start"))

    assert isinstance(result, Deployment)
    assert result.service_id == "srv-abc"
    assert result.deploy_id == "dep-xyz"
    assert result.url == "https://api-abc.onrender.com"


def test_the_create_request_carries_what_section_seven_names():
    made, api = provider()

    made.deploy(Service(name="api", repo="https://github.com/o/r.git",
                        branch="develop", build="npm ci", start="npm start"))

    body = api.body_of("/services")
    assert body["type"] == "web_service"
    assert body["repo"] == "https://github.com/o/r.git"
    assert body["branch"] == "develop"
    assert body["ownerId"] == OWNER
    details = body["serviceDetails"]["envSpecificDetails"]
    assert details["buildCommand"] == "npm ci"
    assert details["startCommand"] == "npm start"


def test_the_sealed_values_are_injected_through_the_api():
    """Section 7 stage 5: through the API only, never committed."""
    made, api = provider()
    sealed = {"PORT": "3000", "STRIPE_KEY": "sk_live_value"}

    made.deploy(Service(name="api", repo="r", branch="main", build="b",
                        start="s", env=sealed))

    body = api.body_of("/services")
    assert body["envVars"] == [
        {"key": "PORT", "value": "3000"},
        {"key": "STRIPE_KEY", "value": "sk_live_value"},
    ]


def test_a_service_with_no_environment_still_creates():
    made, api = provider()

    made.deploy(Service(name="api", repo="r", branch="main", build="b", start="s"))

    assert api.body_of("/services")["envVars"] == []


def test_the_key_travels_as_a_bearer_token():
    made, api = provider()

    made.deploy(Service(name="api", repo="r", branch="main", build="b", start="s"))

    assert api.headers[0]["Authorization"] == f"Bearer {KEY}"


def test_the_workspace_is_looked_up_once_and_reused():
    made, api = provider()
    service = Service(name="api", repo="r", branch="main", build="b", start="s")

    made.deploy(service)
    made.deploy(service)

    owners = [url for _, url, _ in api.calls if "/owners" in url]
    assert len(owners) == 1


def test_a_supplied_workspace_is_not_looked_up():
    api = Api({"/services": reply(201, CREATED)})
    made = Render(api_key=KEY, owner=OWNER, fetch=api)

    made.deploy(Service(name="api", repo="r", branch="main", build="b", start="s"))

    assert not [url for _, url, _ in api.calls if "/owners" in url]


def test_an_api_key_with_no_workspace_is_refused():
    made, _ = provider({"/owners": reply(200, [])})

    with pytest.raises(RenderError):
        made.deploy(Service(name="api", repo="r", branch="main", build="b", start="s"))


def test_a_creation_that_returns_no_ids_is_refused():
    made, _ = provider({"/owners": reply(200, OWNERS),
                        "/services": reply(201, {"service": {}})})

    with pytest.raises(RenderError):
        made.deploy(Service(name="api", repo="r", branch="main", build="b", start="s"))


def test_an_api_error_carries_render_s_own_words():
    made, _ = provider({"/owners": reply(200, OWNERS),
                        "/services": reply(400, {"message": "name already in use"})})

    with pytest.raises(RenderError) as caught:
        made.deploy(Service(name="api", repo="r", branch="main", build="b", start="s"))

    assert "name already in use" in str(caught.value)


def test_environment_values_can_be_replaced_later():
    made, api = provider({"/owners": reply(200, OWNERS),
                          "/env-vars": reply(200, [])})

    made.set_env("srv-abc", {"PORT": "8080"})

    body = api.body_of("/env-vars")
    assert body == [{"key": "PORT", "value": "8080"}]


# --------------------------------------------------------------------------
# storing the three identifiers where module 1.3 reserved them
# --------------------------------------------------------------------------


def test_the_identifiers_are_stored_under_module_1_3s_keys(tmp_path: Path):
    made = Deployment(service_id="srv-abc", deploy_id="dep-xyz",
                      url="https://api-abc.onrender.com")

    path = store(tmp_path, made)

    values = projectstate.read_project_state(tmp_path)
    assert values[projectstate.SERVICE_ID_KEY] == "srv-abc"
    assert values[projectstate.DEPLOY_ID_KEY] == "dep-xyz"
    assert values[projectstate.SERVICE_URL_KEY] == "https://api-abc.onrender.com"
    assert path.name == projectstate.PROJECT_STATE_FILENAME


def test_no_new_key_or_location_is_invented(tmp_path: Path):
    store(tmp_path, Deployment("srv", "dep", "https://x"))

    assert set(projectstate.read_project_state(tmp_path)) <= set(
        projectstate.KNOWN_STATE_KEYS)


# --------------------------------------------------------------------------
# 6.6, the eleven states Render actually reports
# --------------------------------------------------------------------------


def test_every_status_render_documents_is_mapped():
    """Section 7 names three. Render reports eleven, and all are handled."""
    assert set(STATES) == {
        "created", "queued", "build_in_progress", "update_in_progress", "live",
        "deactivated", "build_failed", "update_failed", "canceled",
        "pre_deploy_in_progress", "pre_deploy_failed",
    }
    assert len(STATES) == 11


@pytest.mark.parametrize(
    "state", ["created", "queued", "build_in_progress", "update_in_progress",
              "pre_deploy_in_progress"])
def test_a_deploy_still_working_reads_as_building(state: str):
    made, _ = provider({"/deploys/": reply(200, {"id": "dep", "status": state})})

    assert made.poll_status("srv/dep") is Status.BUILDING


def test_a_live_deploy_reads_as_live():
    made, _ = provider({"/deploys/": reply(200, {"id": "dep", "status": "live"})})

    assert made.poll_status("srv/dep") is Status.LIVE


@pytest.mark.parametrize(
    "state", ["build_failed", "update_failed", "pre_deploy_failed",
              "canceled", "deactivated"])
def test_a_deploy_that_ended_badly_reads_as_failed(state: str):
    made, _ = provider({"/deploys/": reply(200, {"id": "dep", "status": state})})

    assert made.poll_status("srv/dep") is Status.FAILED


def test_a_status_render_adds_later_keeps_the_monitor_waiting():
    """Better to keep polling than to declare a result that cannot be justified."""
    made, _ = provider({"/deploys/": reply(200, {"id": "dep", "status": "brand_new"})})

    assert made.poll_status("srv/dep") is Status.BUILDING


def test_a_deploy_is_addressed_by_its_service_and_its_id():
    made, api = provider({"/deploys/": reply(200, {"status": "live"})})

    made.poll_status("srv-abc/dep-xyz")

    assert any("/services/srv-abc/deploys/dep-xyz" in url for _, url, _ in api.calls)


def test_a_deploy_id_with_no_service_is_refused():
    made, _ = provider({})

    with pytest.raises(RenderError) as caught:
        made.poll_status("dep-only")

    assert "service/deploy" in str(caught.value)


def test_build_logs_are_fetched_for_the_service():
    made, api = provider({"/owners": reply(200, OWNERS), "/logs": reply(200, {
        "logs": [{"message": "Step 1/3"}, {"message": "COPY failed: file not found"}],
        "hasMore": False})})

    logs = made.get_logs("srv-abc/dep-xyz")

    assert "COPY failed" in logs
    url = next(url for _, url, _ in api.calls if "/logs" in url)
    assert "type=build" in url
    assert "resource=srv-abc" in url


def test_no_logs_reads_as_empty_rather_than_failing():
    made, _ = provider({"/owners": reply(200, OWNERS),
                        "/logs": reply(200, {"logs": []})})

    assert made.get_logs("srv/dep") == ""


# --------------------------------------------------------------------------
# the monitor, against the interface rather than against Render
# --------------------------------------------------------------------------


class Fake:
    """A provider that answers from a script, with no network anywhere."""

    def __init__(self, states: list[Status], logs: str = "") -> None:
        self.states = list(states)
        self.logs = logs
        self.asked = 0

    def poll_status(self, deploy_id: str) -> Status:
        self.asked += 1
        return self.states.pop(0) if self.states else Status.BUILDING

    def get_logs(self, deploy_id: str) -> str:
        return self.logs


def clocked():
    """A clock a test moves rather than waits on."""
    ticks = [0.0]
    return ticks, (lambda: ticks[0]), (lambda s: ticks.__setitem__(0, ticks[0] + s))


def test_the_interval_and_the_limit_are_what_section_seven_says():
    assert EVERY == 15.0
    assert LIMIT == 600.0


def test_a_deploy_that_goes_live_is_reported_live():
    ticks, now, sleep = clocked()
    fake = Fake([Status.BUILDING, Status.BUILDING, Status.LIVE])

    result = watch(fake, "srv/dep", sleep=sleep, clock=now)

    assert result.outcome is Outcome.LIVE
    assert result.ok is True
    assert result.polls == 3


def test_polling_waits_the_documented_interval():
    ticks, now, sleep = clocked()
    fake = Fake([Status.BUILDING, Status.LIVE])

    watch(fake, "srv/dep", sleep=sleep, clock=now)

    assert ticks[0] == EVERY


def test_a_failed_deploy_is_classified_by_module_6_3s_taxonomy():
    ticks, now, sleep = clocked()
    fake = Fake([Status.FAILED],
                logs="Step 3/3 : RUN npm ci\n"
                     "The command '/bin/sh -c npm ci' returned a non-zero code: 1")

    result = watch(fake, "srv/dep", sleep=sleep, clock=now)

    assert result.outcome is Outcome.FAILED
    assert result.fault is Fault.DEPENDENCY
    assert result.actionable is True
    assert result.review is False


def test_the_taxonomy_is_imported_rather_than_restated():
    """The categories and the patterns are defined once, in module 6.3."""
    source = Path(monitor.__file__).read_text(encoding="utf-8")

    assert "from prodpilot.buildtest import Fault, classify" in source
    assert "class Fault" not in source, "a second taxonomy was declared"
    assert "re.compile" not in source, "a second set of patterns was declared"
    assert monitor.Fault is Fault
    assert monitor.classify.__module__ == "prodpilot.buildtest"


def test_a_failure_the_taxonomy_does_not_cover_goes_to_review():
    ticks, now, sleep = clocked()
    fake = Fake([Status.FAILED], logs="the region ran out of capacity")

    result = watch(fake, "srv/dep", sleep=sleep, clock=now)

    assert result.outcome is Outcome.FAILED
    assert result.fault is None
    assert result.review is True
    assert result.actionable is False


def test_a_failure_with_no_logs_goes_to_review():
    ticks, now, sleep = clocked()

    result = watch(Fake([Status.FAILED], logs=""), "srv/dep", sleep=sleep, clock=now)

    assert result.fault is None
    assert result.review is True
    assert "no build logs" in result.detail


def test_logs_that_cannot_be_read_leave_the_failure_unclassified():
    class Broken(Fake):
        def get_logs(self, deploy_id):
            raise RuntimeError("the log endpoint refused")

    ticks, now, sleep = clocked()

    result = watch(Broken([Status.FAILED]), "srv/dep", sleep=sleep, clock=now)

    assert result.outcome is Outcome.FAILED
    assert result.fault is None
    assert result.review is True


# --------------------------------------------------------------------------
# a timeout is not a failure
# --------------------------------------------------------------------------


def test_a_deploy_that_never_finishes_times_out():
    ticks, now, sleep = clocked()
    fake = Fake([])

    result = watch(fake, "srv/dep", sleep=sleep, clock=now)

    assert result.outcome is Outcome.TIMEOUT
    assert result.ok is False
    assert result.waited <= LIMIT


def test_the_timeout_is_the_documented_ten_minutes():
    ticks, now, sleep = clocked()

    watch(Fake([]), "srv/dep", sleep=sleep, clock=now)

    assert ticks[0] <= LIMIT
    assert ticks[0] > LIMIT - EVERY


def test_a_timeout_is_a_different_outcome_from_a_failure():
    """Render saying failed means the build finished and did not work.

    A timeout means nothing is known and the deploy may still succeed, so a
    caller has to be able to tell them apart without reading a string.
    """
    ticks, now, sleep = clocked()
    timed = watch(Fake([]), "srv/dep", sleep=sleep, clock=now)

    ticks2, now2, sleep2 = clocked()
    failed = watch(Fake([Status.FAILED], logs="COPY failed: file not found"),
                   "srv/dep", sleep=sleep2, clock=now2)

    assert timed.outcome is not failed.outcome
    assert timed.fault is None and failed.fault is not None
    assert timed.review is True


def test_a_shorter_limit_is_respected():
    ticks, now, sleep = clocked()

    result = watch(Fake([]), "srv/dep", every=5, limit=20, sleep=sleep, clock=now)

    assert result.outcome is Outcome.TIMEOUT
    assert result.polls == 5, "polled at 0, 5, 10, 15 and 20 seconds"
    assert ticks[0] == 20


def test_a_provider_that_cannot_be_reached_is_an_error_not_a_failure():
    class Down:
        def poll_status(self, deploy_id):
            raise RuntimeError("the API is unreachable")

        def get_logs(self, deploy_id):
            return ""

    ticks, now, sleep = clocked()

    result = watch(Down(), "srv/dep", sleep=sleep, clock=now)

    assert result.outcome is Outcome.ERROR
    assert result.review is True
    assert "unreachable" in result.detail


def test_the_monitor_takes_any_provider_not_just_render():
    """Which is what module 6.9 will check across all of stage 5 to 8."""
    import inspect

    params = list(inspect.signature(watch).parameters)
    assert params[0] == "provider"
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    assert "import render" not in source
    assert "from prodpilot.render" not in source


def test_the_result_serialises_whole():
    made = Watch("srv/dep", Outcome.FAILED, 3, 45.0, Fault.BASE_IMAGE, "a line")

    payload = made.to_dict()

    assert set(payload) == {
        "deploy_id", "outcome", "polls", "waited", "fault", "detail",
        "review", "actionable",
    }
    json.dumps(payload)


def test_no_provider_in_this_file_uses_the_real_transport():
    """Every one is built with an injected transport, so nothing leaves here."""
    made, api = provider()

    assert made.fetch is api
    assert made.fetch is not render.send
