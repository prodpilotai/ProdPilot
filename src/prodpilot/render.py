"""The Render implementation of the provider interface.

Scope is Phase 6 modules 6.5 and 6.6. Section 7 stage 5 creates the web service
and stage 6 polls it, and Section 7.2 says ProdPush is built against a small
provider interface rather than against Render directly. This is the first real
implementation of that interface, and module 6.1 defined its four methods
before any of this existed so none of it is a retrofit.

Nothing here decides when to stop polling. That is the monitor, which works
against the interface rather than against this class, so a second provider
would need no change there.

Where the request and response shapes come from
------------------------------------------------
Render's published API reference, read rather than remembered. Creating a
service is POST /services taking type, name, ownerId, repo, branch,
serviceDetails with the runtime and its build and start commands, and envVars as
a list of key and value objects, answering 201 with the service under service
and the first deploy id under deployId. A deploy is read from
/services/{id}/deploys/{id} and carries a status from a fixed set of eleven
values. Logs come from /logs, which requires the workspace id and the resource,
and returns entries carrying a message.

Eleven states, not three
-------------------------
Section 7 names build_in_progress, live and failed. Render actually reports
created, queued, build_in_progress, update_in_progress, live, deactivated,
build_failed, update_failed, canceled, pre_deploy_in_progress and
pre_deploy_failed. STATES maps all eleven onto the three the provider interface
already had, so the extra eight are handled rather than falling through as
unknown, and a caller still sees the three states Section 7 describes.

Why the environment never travels any other way
------------------------------------------------
Stage 5 says the sealed values are injected through the API only and never
committed. They go in the create request as envVars and nowhere else. Module 6.4
has already pushed, and the sealed file was never staged, so by the time this
runs the values exist only on this machine and in the request body.

Testing without touching Render
--------------------------------
The transport is injected, the same way module 5.1 injects one for GitHub. A
test supplies a callable that returns a Reply, so every path here runs without
the network and without an API key.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from prodpilot import projectstate
from prodpilot.config import ConfigError, load_credentials
from prodpilot.provider import Deployment, Service, Status

logger = logging.getLogger(__name__)

API = "https://api.render.com/v1"
AGENT = "prodpilot-prodpush"

# Every deploy status Render's reference lists, mapped onto the three states the
# provider interface carries. Anything Render adds later that is not here is
# treated as still building, which keeps the monitor polling rather than
# declaring a result it cannot justify.
STATES: dict[str, Status] = {
    "created": Status.BUILDING,
    "queued": Status.BUILDING,
    "build_in_progress": Status.BUILDING,
    "update_in_progress": Status.BUILDING,
    "pre_deploy_in_progress": Status.BUILDING,
    "live": Status.LIVE,
    "build_failed": Status.FAILED,
    "update_failed": Status.FAILED,
    "pre_deploy_failed": Status.FAILED,
    "canceled": Status.FAILED,
    "deactivated": Status.FAILED,
}

# Which runtime to ask for. Section 1 supports two stacks and both run on Node.
RUNTIME = "node"


class RenderError(Exception):
    """Raised when the Render API cannot be used."""


@dataclass(frozen=True)
class Reply:
    """One HTTP response, kept small so a test can build one."""

    status: int
    body: bytes

    def json(self):
        # An empty body is a real answer, not a broken one. Deleting a service
        # answers 204 with nothing in it, which is the documented shape, so
        # treating that as invalid JSON would make teardown fail on success.
        # This is the same rule module 6.8's Reply already follows.
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RenderError(f"the response was not valid JSON: {exc}") from exc


Fetch = Callable[[str, str, dict[str, str], bytes | None], Reply]


def send(url: str, method: str, headers: dict[str, str],
         body: bytes | None) -> Reply:
    """The default transport. One request, no retries, no interpretation."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            return Reply(reply.status, reply.read())
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read())
    except urllib.error.URLError as exc:
        raise RenderError(f"cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:
        raise RenderError(f"cannot reach {url}: {exc}") from exc


def key() -> str | None:
    """The stored Render API key, read through module 1.3."""
    try:
        return load_credentials().render_api_key
    except ConfigError as exc:
        logger.warning("cannot read the credential store: %s", exc)
        return None


class Render:
    """Render, behind the four methods the provider interface asks for."""

    def __init__(self, api_key: str | None = None, owner: str | None = None,
                 fetch: Fetch = send) -> None:
        self.api_key = api_key if api_key is not None else key()
        if not self.api_key:
            raise RenderError("no Render API key is stored, run prodpilot setup")
        self.owner = owner
        self.fetch = fetch
        self.calls = 0

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": AGENT,
        }

    def call(self, path: str, method: str = "GET",
             body: Mapping | None = None, params: Mapping | None = None):
        """One API call, with the error surfaced in Render's own words."""
        url = f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        raw = json.dumps(body).encode("utf-8") if body is not None else None

        reply = self.fetch(url, method, self.headers(), raw)
        self.calls += 1

        if 200 <= reply.status < 300:
            return reply.json()
        raise RenderError(f"{method} {path} returned {reply.status}: {said(reply)}")

    def workspace(self) -> str:
        """The workspace id, which creating a service and reading logs require.

        Looked up once when the caller did not supply one, because the
        credential store holds an API key and not a workspace.
        """
        if self.owner:
            return self.owner
        found = self.call("/owners", params={"limit": 1})
        if not isinstance(found, list) or not found:
            raise RenderError("the API key has access to no workspace")
        owner = (found[0] or {}).get("owner") or {}
        if not owner.get("id"):
            raise RenderError("the workspace listing carried no id")
        self.owner = owner["id"]
        return self.owner

    # ---------------------------------------------------------------- 6.5

    def deploy(self, service: Service) -> Deployment:
        """Create the web service and start its first deploy.

        The sealed values travel here in envVars and nowhere else.
        """
        body = {
            "type": "web_service",
            "name": service.name,
            "ownerId": self.workspace(),
            "repo": service.repo,
            "branch": service.branch,
            "serviceDetails": {
                "runtime": RUNTIME,
                "envSpecificDetails": {
                    "buildCommand": service.build,
                    "startCommand": service.start,
                },
            },
            "envVars": [{"key": k, "value": v} for k, v in sorted(service.env.items())],
        }
        made = self.call("/services", method="POST", body=body)
        if not isinstance(made, dict):
            raise RenderError("creating the service returned no object")

        created = made.get("service") or {}
        service_id = created.get("id")
        deploy_id = made.get("deployId")
        if not service_id or not deploy_id:
            raise RenderError("the created service carried no id or deploy id")

        logger.info("created Render service %s with deploy %s", service_id, deploy_id)
        return Deployment(
            service_id=str(service_id),
            deploy_id=str(deploy_id),
            url=str(created.get("url") or ""),
        )

    def set_env(self, service_id: str, env: Mapping[str, str]) -> None:
        """Replace the environment variables on an existing service."""
        body = [{"key": k, "value": v} for k, v in sorted(env.items())]
        self.call(f"/services/{service_id}/env-vars", method="PUT", body=body)
        logger.info("replaced %s environment value(s) on %s", len(body), service_id)

    # ------------------------------------------------------- added for 5.3

    def deploy_at(self, service: Service, commit: str) -> Deployment:
        """Create the service and deploy one exact commit rather than the head.

        Module 5.3 needs this. Its features were extracted from a pinned commit,
        so a label taken from whatever the branch points at today would describe
        different code and the join between the two would be wrong.

        Render's create call carries no commit field. Its reference puts commit
        selection on the trigger deploy call instead, as commitId, so the
        service is created first and then told which commit to build. An empty
        commit falls back to deploy, which is the branch head.
        """
        made = self.deploy(service)
        if not commit:
            return made

        found = self.call(f"/services/{made.service_id}/deploys",
                          method="POST", body={"commitId": commit})
        pinned = (found or {}).get("id") if isinstance(found, dict) else None
        if not pinned:
            raise RenderError(f"the deploy pinned to {commit[:10]} carried no id")

        logger.info("deploying %s at commit %s", made.service_id, commit[:10])
        return Deployment(made.service_id, str(pinned), made.url)

    def remove(self, service_id: str) -> None:
        """Delete a service permanently.

        Module 5.3 creates a service per labelled repository and has to leave
        none of them running, so teardown is part of the provider rather than
        something a caller improvises. Render answers 204 with no body.
        """
        self.call(f"/services/{service_id}", method="DELETE")
        logger.info("deleted Render service %s", service_id)

    # ---------------------------------------------------------------- 6.6

    def poll_status(self, deploy_id: str) -> Status:
        """Where one deploy has got to, in the interface's three states.

        The service is needed to address a deploy, so it is carried alongside
        the deploy id in the form service/deploy.
        """
        service, _, deploy = deploy_id.partition("/")
        if not deploy:
            raise RenderError(
                "a deploy is addressed as service/deploy, since Render scopes "
                f"a deploy to its service, got {deploy_id}")

        found = self.call(f"/services/{service}/deploys/{deploy}")
        if not isinstance(found, dict):
            raise RenderError("the deploy lookup returned no object")
        state = str(found.get("status") or "")
        if state not in STATES:
            logger.warning("unknown Render deploy status %s, still waiting", state)
            return Status.BUILDING
        return STATES[state]

    def get_logs(self, deploy_id: str) -> str:
        """The build logs for one deploy, for classifying a failure."""
        service, _, _ = deploy_id.partition("/")
        found = self.call("/logs", params={
            "ownerId": self.workspace(),
            "resource": service or deploy_id,
            "type": "build",
            "limit": 100,
        })
        entries = (found or {}).get("logs") if isinstance(found, dict) else None
        if not entries:
            return ""
        return "\n".join(str(e.get("message", "")) for e in entries if isinstance(e, dict))


def said(reply: Reply) -> str:
    """The message Render gave, for an error a person has to read."""
    try:
        payload = reply.json()
    except RenderError:
        return reply.body[:200].decode("utf-8", "replace")
    if isinstance(payload, dict):
        for field in ("message", "error", "detail"):
            if isinstance(payload.get(field), str):
                return payload[field]
    return str(payload)[:200]


def store(project: str | Path, made: Deployment) -> Path:
    """Record the three identifiers where module 1.3 already reserves them.

    Section 7 stage 5 says the returned ids are stored locally, and module 1.3
    fixed the keys and the file before anything wrote to them, so nothing new is
    invented here.
    """
    return projectstate.write_project_state(project, {
        projectstate.SERVICE_ID_KEY: made.service_id,
        projectstate.DEPLOY_ID_KEY: made.deploy_id,
        projectstate.SERVICE_URL_KEY: made.url,
    })
