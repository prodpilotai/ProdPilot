"""CI/CD wiring, the eighth and last stage of ProdPush.

Scope is Phase 6 module 6.8. Section 7 stage 8 states it: write
.github/workflows/deploy.yml, set the repository secrets it needs through the
GitHub API by encrypting each value as a libsodium sealed box against the
repository public key, commit, push, and verify through the Actions API that
the pipeline is active.

Deploy first, wire second
--------------------------
Section 7.1 is the reason this is last. A workflow file sitting in a repository
does nothing until the secrets exist and the service is known, so the first
deployment goes through the Render API in module 6.5 and only then is the
pipeline wired with what that returned. This module reads what module 6.5
stored rather than deploying anything itself.

Why the workflow calls Render's API rather than a deploy hook
---------------------------------------------------------------
Section 7 names a RENDER_DEPLOY_HOOK secret. Render publishes a service's deploy
hook only on the Settings tab of its dashboard. Its API reference has no field
or endpoint that returns it, and retrieving it programmatically is an open
request on Render's own community forum. A pipeline built on the hook therefore
needs a person to copy it by hand, and Phase 6's exit criterion is a deployment
wired end to end without manual intervention.

So the workflow triggers each deploy through Render's Trigger Deploy endpoint,
POST /v1/services/{id}/deploys, which module 6.5 already uses and which was
proven against the real API during labelling. It needs the Render API key and
the service id, both of which ProdPilot already holds, so every secret is set by
this module and nothing is copied by hand.

The cost is stated rather than hidden. A Render API key is account wide, which
is a broader secret than a per service hook. It is sealed before it leaves this
machine, stored only as an encrypted repository secret, and never written to a
file, a log or the returned result, and a developer who wants a narrower blast
radius can give ProdPilot a key created for this purpose and revoke it at will.

A service created from a public repository URL is deploy on request only, so
this workflow is the one path by which a push reaches Render and a push never
deploys twice.

Where each value comes from
----------------------------
APP_URL and RENDER_SERVICE_ID are what module 6.5 wrote through module 1.3's
project state, read back through the same convention. RENDER_API_KEY is the key
module 1.3 stores. Nothing invents a second source for any of them.

Why the health check takes a path
----------------------------------
The workflow checks the service after each deploy. An Express service answers
on /health, which module 2.2's OBS-001 requires of it. A built front end is a
static site with no such route and answers at its root, so the caller passes
the path the stack actually serves. The default is /health.

Why the encryption is exactly GitHub's documented pattern
-----------------------------------------------------------
GitHub requires the value encrypted with libsodium against the repository public
key, and publishes the Python for it: read the base64 public key through
PyNaCl's Base64Encoder, seal with SealedBox, base64 the ciphertext. That is what
encrypt does, no more and no less. A sealed box is anonymous and one way, so
nothing here can decrypt what it sends, which is the point.

Why the push is module 6.4's
-----------------------------
.github/workflows/deploy.yml is already in module 6.4's GENERATED set, so this
module writes the file and calls push.run, which stages it under the same rules,
with the same ignore checks and the same commit message.

Why the transport is not module 5.1's client
----------------------------------------------
gh.Client offers GET only, by construction. Setting a secret is a PUT, so this
module carries a small client of its own with an injected transport, the same
shape module 6.5 uses for Render.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot import monitor, projectstate, push
from prodpilot.config import ConfigError, load_credentials

logger = logging.getLogger(__name__)

API = "https://api.github.com"
VERSION = "2022-11-28"
AGENT = "prodpilot-prodpush"

WORKFLOW = ".github/workflows/deploy.yml"
KEY = "RENDER_API_KEY"
SERVICE = "RENDER_SERVICE_ID"
APP_URL = "APP_URL"
SECRETS = (KEY, SERVICE, APP_URL)

# The endpoint the workflow calls, module 6.5's own Trigger Deploy call.
RENDER = "https://api.render.com/v1"

# The workflow waits for its own deploy as stage 6 does, every 15 seconds for
# at most 10 minutes, and treats as ended the deploy states stage 6 maps to
# failed. Only render.py may import Render's module, so the states are written
# here and a test holds them equal to render.STATES.
EVERY = int(monitor.EVERY)
POLLS = int(monitor.LIMIT // monitor.EVERY)
MINUTES = int(monitor.LIMIT // 60)
ENDED = ("build_failed", "canceled", "deactivated", "pre_deploy_failed", "update_failed")

# Small programs the workflow runs on the runner's python3 to read Render's
# JSON from standard input. NEWEST also takes the time of the trigger and
# allows 30 seconds of clock difference between the runner and Render.
READ_ID = 'import json, sys; print(json.load(sys.stdin).get("id") or "")'
READ_STATUS = 'import json, sys; print(json.load(sys.stdin).get("status") or "")'
NEWEST = (
    "import json, sys; from datetime import datetime; "
    'when = lambda d: datetime.fromisoformat(d["createdAt"].replace("Z", "+00:00")).timestamp(); '
    'made = sorted((i["deploy"] for i in json.load(sys.stdin) '
    'if when(i["deploy"]) >= int(sys.argv[1]) - 30), key=when); '
    'print(made[-1]["id"] if made else "")'
)

HEALTH = "/health"

# The Actions API reports one of five states for a workflow. Only the first
# means the pipeline will actually run.
ACTIVE = "active"
STATES = ("active", "deleted", "disabled_fork", "disabled_inactivity",
          "disabled_manually")


class WireError(Exception):
    """Raised when the pipeline cannot be wired."""


@dataclass(frozen=True)
class Reply:
    """One HTTP response, kept small so a test can build one."""

    status: int
    body: bytes

    def json(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WireError(f"the response was not valid JSON: {exc}") from exc


Fetch = Callable[[str, str, dict[str, str], bytes | None], Reply]


@dataclass(frozen=True)
class Wire:
    """What wiring the pipeline achieved."""

    repo: str
    ok: bool
    secrets: tuple[str, ...] = field(default_factory=tuple)
    workflow: str = ""
    state: str = ""
    pushed: bool = False
    detail: str = ""

    @property
    def active(self) -> bool:
        return self.state == ACTIVE

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "ok": self.ok,
            "secrets": list(self.secrets),
            "workflow": self.workflow,
            "state": self.state,
            "active": self.active,
            "pushed": self.pushed,
            "detail": self.detail,
        }

    def summary(self) -> str:
        if self.ok:
            return (f"{self.repo}: pipeline wired and {self.state}, "
                    f"{len(self.secrets)} secret(s) set")
        return f"{self.repo}: pipeline not wired, {self.detail}"


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
        raise WireError(f"cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:
        raise WireError(f"cannot reach {url}: {exc}") from exc


def encrypt(public_key: str, value: str) -> str:
    """Seal a value against a repository public key.

    GitHub's documented pattern, unchanged: the base64 public key is read
    through PyNaCl's Base64Encoder, the value is sealed, and the ciphertext is
    base64 encoded for the request body.
    """
    try:
        from nacl import encoding, public
    except ImportError as exc:
        raise WireError(f"PyNaCl is not installed: {exc}") from exc

    # A key GitHub returned that is not a usable key stops stage 8 with a reason
    # rather than raising out of the pipeline, found by module 7.3's full-chain
    # run. PyNaCl raises ValueError for a key of the wrong length or encoding.
    try:
        key = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
        sealed = public.SealedBox(key).encrypt(value.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise WireError(f"the repository public key is not a valid key: {exc}") from exc
    return base64.b64encode(sealed).decode("utf-8")


def slug(root: Path) -> str | None:
    """The owner and repository name, read from the origin remote."""
    remote = push.origin_url(root)
    if not remote:
        return None
    url = push.https_url(remote)
    if not url:
        return None
    path = urllib.parse.urlparse(url).path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [p for p in path.split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else None


class Actions:
    """The slice of the GitHub API this stage needs."""

    def __init__(self, token: str | None = None, fetch: Fetch = send) -> None:
        self.token = token if token is not None else stored()
        if not self.token:
            raise WireError("no GitHub token is stored, run prodpilot setup")
        self.fetch = fetch
        self.calls = 0

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": VERSION,
            "User-Agent": AGENT,
            "Content-Type": "application/json",
        }

    def call(self, path: str, method: str = "GET", body: Mapping | None = None):
        url = f"{API}{path}"
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        reply = self.fetch(url, method, self.headers(), raw)
        self.calls += 1
        if 200 <= reply.status < 300:
            return reply.json()
        raise WireError(f"{method} {path} returned {reply.status}: {said(reply)}")

    def public_key(self, repo: str) -> tuple[str, str]:
        """The repository public key and its id, for sealing a secret."""
        found = self.call(f"/repos/{repo}/actions/secrets/public-key")
        if not isinstance(found, dict) or not found.get("key") or not found.get("key_id"):
            raise WireError("the public key response carried no key or key id")
        return str(found["key"]), str(found["key_id"])

    def set_secret(self, repo: str, name: str, value: str,
                   key: str, key_id: str) -> None:
        """Store one secret, encrypted, never in plaintext."""
        self.call(f"/repos/{repo}/actions/secrets/{name}", method="PUT", body={
            "encrypted_value": encrypt(key, value),
            "key_id": key_id,
        })
        logger.info("set repository secret %s on %s", name, repo)

    def state_of(self, repo: str, path: str = WORKFLOW) -> str:
        """What the Actions API says about the workflow, by its file path.

        The endpoint accepts a file name in place of a numeric id, which is
        what lets this be asked without knowing the id GitHub assigned.
        """
        name = path.rsplit("/", 1)[-1]
        found = self.call(f"/repos/{repo}/actions/workflows/{name}")
        if not isinstance(found, dict) or not found.get("state"):
            raise WireError("the workflow lookup carried no state")
        return str(found["state"])


def stored() -> str | None:
    """The GitHub token, read through module 1.3 as every other module does."""
    try:
        return load_credentials().github_token
    except ConfigError as exc:
        logger.warning("cannot read the credential store: %s", exc)
        return None


def render_key() -> str | None:
    """The Render API key, read through module 1.3."""
    try:
        return load_credentials().render_api_key
    except ConfigError as exc:
        logger.warning("cannot read the credential store: %s", exc)
        return None


def said(reply: Reply) -> str:
    """The message GitHub gave, for an error a person has to read."""
    try:
        payload = reply.json()
    except WireError:
        return reply.body[:200].decode("utf-8", "replace")
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return str(payload)[:200]


def workflow(branch: str = "main", path: str = HEALTH) -> str:
    """The pipeline that redeploys on a push and then checks the service.

    Every value is read from repository secrets rather than written into the
    file, so nothing secret is ever committed. The check afterwards is what
    makes this a pipeline rather than a fire and forget trigger.

    Before checking, it waits for the very deploy it started, polled as stage
    6 polls, because after a fixed wait the check could pass against the
    previous instance. Render answers the trigger with the new deploy, or with
    202 and no body when the deploy is queued behind another, such as one a
    push started; the deploy waited for is then the newest created since the
    trigger, and none found fails the run rather than guessing.
    """
    auth = '-H "Authorization: Bearer $RENDER_API_KEY" -H "Accept: application/json"'
    ended = "|".join(ENDED)
    return f"""name: Deploy

on:
  push:
    branches: [{branch}]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    env:
      RENDER_API_KEY: ${{{{ secrets.{KEY} }}}}
      SERVICE_ID: ${{{{ secrets.{SERVICE} }}}}
    steps:
      - name: Trigger the Render deploy
        id: trigger
        run: |
          since=$(date -u +%s)
          code=$(curl --silent --show-error -o deploy.json -w "%{{http_code}}" -X POST {auth} "{RENDER}/services/$SERVICE_ID/deploys")
          if [ "$code" = "201" ]; then
            deploy=$(python3 -c '{READ_ID}' < deploy.json)
          elif [ "$code" = "202" ]; then
            curl --fail --silent --show-error {auth} "{RENDER}/services/$SERVICE_ID/deploys?limit=100" > deploys.json
            deploy=$(python3 -c '{NEWEST}' "$since" < deploys.json)
          else
            echo "Render refused the deploy with HTTP $code"
            cat deploy.json
            exit 1
          fi
          if [ -z "$deploy" ]; then
            echo "Render accepted the deploy, but no deploy created since the trigger was found"
            exit 1
          fi
          echo "deploy=$deploy" >> "$GITHUB_OUTPUT"

      - name: Wait for that deploy to go live
        env:
          DEPLOY_ID: ${{{{ steps.trigger.outputs.deploy }}}}
        run: |
          for attempt in $(seq 1 {POLLS}); do
            status=$(curl --fail --silent --show-error {auth} "{RENDER}/services/$SERVICE_ID/deploys/$DEPLOY_ID" | python3 -c '{READ_STATUS}' || true)
            case "$status" in
              live)
                echo "deploy $DEPLOY_ID is live"
                exit 0
                ;;
              {ended})
                echo "deploy $DEPLOY_ID ended as $status"
                exit 1
                ;;
            esac
            echo "deploy $DEPLOY_ID is ${{status:-not known yet}}, waiting"
            sleep {EVERY}
          done
          echo "deploy $DEPLOY_ID did not go live within {MINUTES} minutes"
          exit 1

      - name: Check the deployed service answers on {path}
        run: |
          for attempt in 1 2 3 4 5 6 7 8; do
            if curl --fail --silent --show-error "${{{{ secrets.{APP_URL} }}}}{path}" > /dev/null; then
              echo "the service answered on {path}"
              exit 0
            fi
            echo "no answer yet, waiting"
            sleep 15
          done
          echo "the service did not answer on {path}"
          exit 1
"""


def app_url(root: Path) -> str | None:
    """The service URL module 6.5 stored, read back the same way."""
    values = projectstate.read_project_state(root)
    return values.get(projectstate.SERVICE_URL_KEY) or None


def service_id(root: Path) -> str | None:
    """The service id module 6.5 stored, read back the same way."""
    values = projectstate.read_project_state(root)
    return values.get(projectstate.SERVICE_ID_KEY) or None


def wire(root: str | Path, token: str | None = None, fetch: Fetch = send,
         repo: str | None = None, branch: str = "main", path: str = HEALTH,
         key: str | None = None) -> Wire:
    """Write the workflow, set its secrets, push it, and verify it is active.

    path is where the deployed service answers when it is working, /health for
    an Express service and the root for a built front end. key is the Render API
    key, read from module 1.3 when not given.

    Never raises. A caller reads the result to decide what to tell a developer.
    """
    base = Path(root)
    name = repo or slug(base) or (base.name or str(base))

    if not base.is_dir():
        return Wire(name, False, detail=f"{base} is not a project directory")

    target = repo or slug(base)
    if not target:
        return Wire(name, False,
                    detail="the origin remote does not name a GitHub owner and repository")

    url = app_url(base)
    ident = service_id(base)
    if not url or not ident:
        missing = projectstate.SERVICE_URL_KEY if not url else projectstate.SERVICE_ID_KEY
        return Wire(target, False,
                    detail=f"no {missing} in {projectstate.PROJECT_STATE_FILENAME}, "
                           f"run stage 5 first")

    secret = key if key is not None else render_key()
    if not secret:
        return Wire(target, False,
                    detail="no Render API key is stored, run prodpilot setup")

    try:
        actions = Actions(token=token, fetch=fetch)
    except WireError as exc:
        return Wire(target, False, detail=str(exc))

    # Written before the secrets are set so the push carries the finished file.
    target_file = base / WORKFLOW
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(workflow(branch, path), encoding="utf-8")
    except OSError as exc:
        return Wire(target, False, detail=f"cannot write {WORKFLOW}: {exc}")

    values = {KEY: secret, SERVICE: ident, APP_URL: url}
    try:
        public_key, key_id = actions.public_key(target)
        for secret_name in SECRETS:
            actions.set_secret(target, secret_name, values[secret_name],
                               public_key, key_id)
    except WireError as exc:
        return Wire(target, False, workflow=WORKFLOW, detail=str(exc))

    sent = push.run(base, token=actions.token, branch=branch)
    if not sent.ok:
        return Wire(target, False, secrets=SECRETS, workflow=WORKFLOW,
                    detail=f"the workflow could not be pushed: {sent.detail}")

    try:
        state = actions.state_of(target, WORKFLOW)
    except WireError as exc:
        return Wire(target, False, secrets=SECRETS, workflow=WORKFLOW,
                    pushed=True, detail=f"the pipeline could not be verified: {exc}")

    result = Wire(target, state == ACTIVE, secrets=SECRETS,
                  workflow=WORKFLOW, state=state, pushed=True,
                  detail="" if state == ACTIVE else f"the workflow is {state}, not active")
    logger.info("%s", result.summary())
    return result
