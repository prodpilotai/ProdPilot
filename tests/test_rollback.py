"""Redeploy and rollback on Render, against scripted API responses.

The responses follow Render's API reference: Retrieve service, Trigger deploy
(201 with the deploy, or 202 with no body when queued), List deploys (a list of
{cursor, deploy}) and Roll back deploy (201 with the rollback deploy).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from prodpilot import provider as providers
from prodpilot.provider import Provider, Redeploy, Rollback
from prodpilot.render import Render, RenderError, Reply

KEY = "made-up-render-key"
SERVICE = "srv-demo"
URL = "https://demo-api.onrender.com"


def stamp(seconds_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - seconds_ago, tz=timezone.utc).isoformat()


def deploy(ident: str, status: str, seconds_ago: float, trigger: str = "api") -> dict:
    return {"cursor": ident, "deploy": {"id": ident, "status": status, "trigger": trigger,
                                         "createdAt": stamp(seconds_ago)}}


class Api:
    """Render scripted by method and path, recording every call."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, url, method, headers, body) -> Reply:
        self.calls.append((method, url, json.loads(body) if body else None))
        path = url.split("/v1", 1)[1].split("?", 1)[0]
        answer = self.routes.get((method, path))
        if answer is None:
            raise AssertionError(f"unscripted call {method} {path}")
        status, payload = answer
        return Reply(status, b"" if payload is None else json.dumps(payload).encode())


def render(routes) -> tuple[Render, Api]:
    api = Api(routes)
    return Render(api_key=KEY, fetch=api), api


SERVICE_BODY = {"id": SERVICE, "type": "web_service", "serviceDetails": {"url": URL}}


# ---------------------------------------------------------------- the capabilities

def test_the_provider_interface_keeps_its_four_methods():
    assert providers.METHODS == ("deploy", "poll_status", "get_logs", "set_env")


def test_render_offers_both_optional_capabilities():
    made, _ = render({})
    assert isinstance(made, Provider)
    assert isinstance(made, Redeploy) and isinstance(made, Rollback)


def test_a_provider_without_them_is_still_a_provider():
    class Plain:
        def deploy(self, service): ...
        def poll_status(self, deploy_id): ...
        def get_logs(self, deploy_id): ...
        def set_env(self, service_id, env): ...

    assert isinstance(Plain(), Provider)
    assert not isinstance(Plain(), Rollback) and not isinstance(Plain(), Redeploy)


# ---------------------------------------------------------------- find

def test_find_returns_the_address_of_an_existing_service():
    made, _ = render({("GET", f"/services/{SERVICE}"): (200, SERVICE_BODY)})
    assert made.find(SERVICE) == URL


def test_find_returns_none_for_a_deleted_service():
    made, _ = render({("GET", f"/services/{SERVICE}"): (404, {"message": "not found"})})
    assert made.find(SERVICE) is None


def test_find_raises_on_any_other_error():
    made, _ = render({("GET", f"/services/{SERVICE}"): (500, {"message": "boom"})})
    with pytest.raises(RenderError, match="500"):
        made.find(SERVICE)


# ---------------------------------------------------------------- redeploy

def test_redeploy_takes_the_deploy_id_from_a_201():
    made, api = render({
        ("POST", f"/services/{SERVICE}/deploys"): (201, {"id": "dep-new", "status": "created"}),
        ("GET", f"/services/{SERVICE}"): (200, SERVICE_BODY),
    })
    done = made.redeploy(SERVICE)
    assert (done.service_id, done.deploy_id, done.url) == (SERVICE, "dep-new", URL)
    assert api.calls[0][:1] == ("POST",)


def test_a_queued_redeploy_is_found_as_the_newest_deploy_since_the_trigger():
    made, _ = render({
        ("POST", f"/services/{SERVICE}/deploys"): (202, None),
        ("GET", f"/services/{SERVICE}/deploys"): (200, [
            deploy("dep-queued", "queued", 1), deploy("dep-old", "live", 3600)]),
        ("GET", f"/services/{SERVICE}"): (200, SERVICE_BODY),
    })
    assert made.redeploy(SERVICE).deploy_id == "dep-queued"


def test_a_queued_redeploy_with_no_new_deploy_is_an_error_not_a_guess():
    made, _ = render({
        ("POST", f"/services/{SERVICE}/deploys"): (202, None),
        ("GET", f"/services/{SERVICE}/deploys"): (200, [deploy("dep-old", "live", 3600)]),
    })
    with pytest.raises(RenderError, match="no deploy created since the trigger"):
        made.redeploy(SERVICE)


# ---------------------------------------------------------------- previous live deploy

def test_the_previous_live_deploy_is_the_newest_that_served_before_the_current_one():
    made, _ = render({("GET", f"/services/{SERVICE}/deploys"): (200, [
        deploy("dep-broken", "live", 60),
        deploy("dep-failed", "build_failed", 600),
        deploy("dep-good", "deactivated", 1200),
        deploy("dep-older", "deactivated", 7200),
    ])})
    assert made.previous_live(SERVICE, "dep-broken") == "dep-good"


def test_a_first_deploy_has_no_previous_live_deploy():
    made, _ = render({("GET", f"/services/{SERVICE}/deploys"): (200, [
        deploy("dep-first", "live", 60), deploy("dep-cancelled", "canceled", 30)])})
    assert made.previous_live(SERVICE, "dep-first") is None


def test_a_current_deploy_that_cannot_be_found_is_an_error():
    made, _ = render({("GET", f"/services/{SERVICE}/deploys"): (200, [
        deploy("dep-good", "deactivated", 1200)])})
    with pytest.raises(RenderError, match="cannot be identified"):
        made.previous_live(SERVICE, "dep-missing")


# ---------------------------------------------------------------- rollback

def test_rollback_posts_the_deploy_id_and_returns_the_rollback_deploy():
    made, api = render({("POST", f"/services/{SERVICE}/rollback"): (
        201, {"id": "dep-rollback", "status": "created", "trigger": "rollback"})})
    assert made.rollback(SERVICE, "dep-good") == "dep-rollback"
    method, url, body = api.calls[0]
    assert method == "POST" and url.endswith(f"/services/{SERVICE}/rollback")
    assert body == {"deployId": "dep-good"}


def test_a_refused_rollback_raises_in_renders_words():
    made, _ = render({("POST", f"/services/{SERVICE}/rollback"): (
        400, {"message": "deploy cannot be rolled back to"})})
    with pytest.raises(RenderError, match="deploy cannot be rolled back to"):
        made.rollback(SERVICE, "dep-good")
