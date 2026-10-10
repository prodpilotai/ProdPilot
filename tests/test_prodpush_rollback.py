"""ProdPush redeploys a stored service and rolls it back when its smoke test fails.

Render is scripted by method and path, in the shapes its API reference gives. The
pipeline is the real one from the gate to the smoke test, on the gated demo.
"""

from __future__ import annotations

import itertools
import json

import pytest
from typer.testing import CliRunner

from prodpilot import prodpush, projectstate, smoke
from prodpilot.cli import app
from prodpilot.render import Render, Reply

from test_prodpush import HEADERS, KEY, LIVE, TOKEN, github_api, healthy, shippable  # noqa: F401
from test_rollback import deploy

SERVICE = "srv-demo"
SERVICE_BODY = {"id": SERVICE, "type": "web_service", "serviceDetails": {"url": LIVE}}
OWNERS = [{"owner": {"id": "tea-demo", "name": "Team", "email": "t@example.com",
                     "type": "team"}, "cursor": "c"}]


class Api:
    """Render scripted by method and path, with a status per deploy."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, object]],
                 statuses: dict[str, str] | None = None) -> None:
        self.routes = routes
        self.statuses = statuses or {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, method, headers, body) -> Reply:
        path = url.split("/v1", 1)[1].split("?", 1)[0]
        self.calls.append((method, path))
        if method == "GET" and path.startswith(f"/services/{SERVICE}/deploys/"):
            ident = path.rsplit("/", 1)[1]
            return Reply(200, json.dumps({"id": ident,
                                          "status": self.statuses.get(ident, "live")}).encode())
        answer = self.routes.get((method, path))
        if answer is None:
            raise AssertionError(f"unscripted Render call {method} {path}")
        status, payload = answer
        return Reply(status, b"" if payload is None else json.dumps(payload).encode())

    def count(self, method: str, path: str) -> int:
        return sum(1 for m, p in self.calls if (m, p) == (method, path))


def redeploy_routes(extra: dict | None = None) -> dict:
    routes = {
        ("GET", f"/services/{SERVICE}"): (200, SERVICE_BODY),
        ("POST", f"/services/{SERVICE}/deploys"): (201, {"id": "dep-broken"}),
        ("GET", f"/services/{SERVICE}/deploys"): (200, [
            deploy("dep-broken", "live", 60), deploy("dep-good", "deactivated", 3600)]),
        ("POST", f"/services/{SERVICE}/rollback"): (201, {"id": "dep-rollback",
                                                          "trigger": "rollback"}),
        ("GET", "/owners"): (200, OWNERS),
        ("POST", "/services"): (201, {"service": SERVICE_BODY, "deployId": "dep-first"}),
    }
    routes.update(extra or {})
    return routes


class Site:
    """The deployed service: broken until the rollback, then healthy if asked to be."""

    def __init__(self, api: Api, heals: bool = True) -> None:
        self.api = api
        self.heals = heals
        self.asked: list[str] = []

    def __call__(self, url: str) -> smoke.Answer:
        self.asked.append(url)
        rolled = self.api.count("POST", f"/services/{SERVICE}/rollback") > 0
        if rolled and self.heals:
            return smoke.Answer(200, dict(HEADERS), '{"status":"ok"}')
        if url.endswith("/health") and not rolled:
            return smoke.Answer(200, dict(HEADERS), '{"status":"ok"}')
        return smoke.Answer(500, dict(HEADERS), "Error: boom\n    at handler (/app/x.js:1:1)")


def stored(root) -> None:
    projectstate.write_project_state(root, {projectstate.SERVICE_ID_KEY: SERVICE,
                                            projectstate.DEPLOY_ID_KEY: "dep-good",
                                            projectstate.SERVICE_URL_KEY: LIVE})


def ship(root, api: Api, site=None, **overrides):
    from nacl import encoding, public

    key = public.PrivateKey.generate().public_key.encode(encoding.Base64Encoder()).decode()
    hub, secrets = github_api(key)
    options = dict(provider=Render(api_key=KEY, fetch=api), fetch=site or Site(api),
                   github=hub, token=TOKEN, build=healthy,
                   sleep=lambda s: None, clock=lambda: 0.0)
    options.update(overrides)
    return prodpush.run(root, **options), secrets


def test_a_failed_redeploy_is_rolled_back_to_the_previous_live_deploy(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes())

    shipped, secrets = ship(root, api)

    assert shipped.failed.stage is prodpush.Stage.SMOKE
    assert shipped.rollback["outcome"] == "restored"
    assert shipped.rollback["to"] == "dep-good" and shipped.rollback["deploy"] == "dep-rollback"
    assert "Rolled back to deploy dep-good, which is live again" in shipped.failed.detail
    assert "redeployed existing service srv-demo" in shipped.steps[5].detail
    assert api.count("POST", "/services") == 0, "no second service is created"
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 1
    reached = {s.stage: s.ran for s in shipped.steps}
    assert reached[prodpush.Stage.CICD] is False and secrets == {}
    assert shipped.to_dict()["rollback"]["outcome"] == "restored"


def test_a_first_deploy_is_not_rolled_back(shippable):
    root, _ = shippable
    api = Api(redeploy_routes())

    shipped, _ = ship(root, api)

    assert shipped.failed.stage is prodpush.Stage.SMOKE
    assert shipped.rollback["outcome"] == "first_deploy"
    assert "first deploy, so there was no earlier version" in shipped.failed.detail
    assert api.count("POST", "/services") == 1
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 0


def test_a_failed_rollback_reports_both_failures_and_stops(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes({("POST", f"/services/{SERVICE}/rollback"): (
        400, {"message": "deploy cannot be rolled back to"})}))

    shipped, secrets = ship(root, api)

    detail = shipped.failed.detail
    assert shipped.rollback["outcome"] == "rollback_failed"
    assert "Rolling back to deploy dep-good failed too" in detail
    assert "deploy cannot be rolled back to" in detail
    assert "https://dashboard.render.com/web/srv-demo" in detail
    assert detail.index("Rolling back") > 0, "the smoke test's own report comes first"
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 1, "never retried"
    assert secrets == {}


def test_a_rollback_that_never_goes_live_times_out_once(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes(), statuses={"dep-rollback": "update_in_progress"})
    ticks = itertools.count(0, 30)

    shipped, _ = ship(root, api, clock=lambda: float(next(ticks)))

    assert shipped.rollback["outcome"] == "rollback_failed"
    assert "did not go live" in shipped.failed.detail
    assert "dashboard.render.com" in shipped.failed.detail
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 1


def test_a_rollback_whose_health_check_still_fails_is_reported(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes())

    shipped, _ = ship(root, api, site=Site(api, heals=False))

    assert shipped.rollback["outcome"] == "health_failed"
    assert "live again, but /health still does not answer" in shipped.failed.detail
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 1


def test_no_rollback_keeps_the_1_0_0_behaviour(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes())

    shipped, _ = ship(root, api, rollback=False)

    assert shipped.failed.stage is prodpush.Stage.SMOKE
    assert shipped.rollback == {}
    assert "Rolled back" not in shipped.failed.detail
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 0


def test_a_deleted_stored_service_is_replaced_by_a_new_one(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes({("GET", f"/services/{SERVICE}"): (404, {"message": "gone"})}))

    shipped, _ = ship(root, api)

    assert api.count("POST", "/services") == 1
    assert shipped.rollback["outcome"] == "first_deploy"


def test_a_healthy_redeploy_goes_on_to_wire_ci_cd(shippable):
    root, _ = shippable
    stored(root)
    api = Api(redeploy_routes())

    def site(url: str) -> smoke.Answer:
        return smoke.Answer(200, dict(HEADERS), '{"status":"ok"}')

    shipped, secrets = ship(root, api, site=site)

    assert shipped.ok is True, shipped.summary()
    assert shipped.rollback == {}
    assert api.count("POST", f"/services/{SERVICE}/rollback") == 0
    assert set(secrets)


def test_the_deploy_command_takes_no_rollback(monkeypatch, tmp_path):
    seen = {}

    def fake(project, branch="main", rollback=True, **kwargs):
        seen.update(project=project, branch=branch, rollback=rollback)
        return prodpush.Ship("demo", (prodpush.Step(prodpush.Stage.GATE, False, "score 50"),))

    monkeypatch.setattr(prodpush, "run", fake)
    done = CliRunner().invoke(app, ["deploy", str(tmp_path), "--no-rollback"])
    assert done.exit_code == 1 and seen["rollback"] is False
    done = CliRunner().invoke(app, ["deploy", str(tmp_path)])
    assert seen["rollback"] is True


@pytest.mark.parametrize("given,expected", [({}, True), ({"rollback": False}, False)])
async def test_the_deploy_tool_takes_an_optional_rollback(monkeypatch, given, expected):
    from mcp.server import MCPServer  # noqa: F401
    from prodpilot import server

    seen = {}

    def fake(project, branch="main", rollback=True, **kwargs):
        seen["rollback"] = rollback
        return prodpush.Ship("demo")

    monkeypatch.setattr(prodpush, "run", fake)
    built = server.build_server()
    await built.call_tool("prodpilot_deploy", {"project_path": ".", **given})
    assert seen["rollback"] is expected


def test_a_second_run_with_nothing_new_to_commit_still_pushes(shippable):
    """The generated files are committed already and only the state file is untracked."""
    from prodpilot import push

    root, mirror = shippable
    stored(root)
    sent = push.run(root, token=TOKEN)
    assert sent.ok is True, sent.detail
    code, _ = push.git(mirror, "rev-parse", "--verify", "main")
    assert code == 0
