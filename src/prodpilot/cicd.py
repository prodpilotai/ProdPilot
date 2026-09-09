"""CI/CD wiring, the eighth and last stage of ProdPush.

Scope is Phase 6 module 6.8. Section 7 stage 8 states it: rewrite
.github/workflows/deploy.yml with the real RENDER_DEPLOY_HOOK and APP_URL, set
repository secrets programmatically through the GitHub API by encrypting each
value as a libsodium sealed box against the repository public key, commit, push,
and verify through the Actions API that the pipeline is active.

Deploy first, wire second
--------------------------
Section 7.1 is the reason this is last. A workflow file sitting in a repository
does nothing until the secrets exist and the service is known, so the first
deployment goes through the Render API in module 6.5 and only then is the
pipeline wired with what that returned. The build order of this phase already
respects that, and this module reads the service URL module 6.5 stored rather
than deploying anything itself.

Where each value comes from
----------------------------
APP_URL is the service URL module 6.5 wrote through module 1.3's project state,
read back through the same convention. Nothing invents a second source for it.

RENDER_DEPLOY_HOOK is supplied by the caller. Render's own documentation says
the deploy hook is a secret found on the service's Settings tab in the
dashboard, and does not document retrieving it through the REST API, so it is
taken as an argument rather than a URL format guessed from a service id. Getting
that wrong would write a broken pipeline that looks correct.

Why the encryption is exactly GitHub's documented pattern
-----------------------------------------------------------
GitHub requires the value encrypted with libsodium against the repository public
key, and publishes the Python for it: read the base64 public key through
PyNaCl's Base64Encoder, seal with SealedBox, base64 the ciphertext. That is what
encrypt does, no more and no less. A sealed box is anonymous and one way, so
nothing here can decrypt what it sends, which is the point.

A plaintext secret never reaches a file, a log or the returned result. Only the
ciphertext is sent, and the result carries the names of the secrets set and
never their values.

Why the push is module 6.4's
-----------------------------
The workflow file is a ProdPilot generated file, and .github/workflows/deploy.yml
is already in module 6.4's GENERATED set because the DYNAMIC-PARAMETRIC shape
that creates it declares that path. So this module writes the file and calls
push.run, which stages it under the same rules, with the same ignore checks and
the same commit message. A second push mechanism would be a second place to get
staging wrong.

Why the transport is not module 5.1's client
----------------------------------------------
gh.Client offers GET only, by construction. Setting a secret is a PUT, so this
module carries a small client of its own with an injected transport, the same
shape module 6.5 uses for Render. Nothing in module 5.1 is changed to
accommodate it.
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

from prodpilot import projectstate, push
from prodpilot.config import ConfigError, load_credentials

logger = logging.getLogger(__name__)

API = "https://api.github.com"
VERSION = "2022-11-28"
AGENT = "prodpilot-prodpush"

WORKFLOW = ".github/workflows/deploy.yml"
HOOK = "RENDER_DEPLOY_HOOK"
APP_URL = "APP_URL"

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

    key = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(key).encrypt(value.encode("utf-8"))
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


def said(reply: Reply) -> str:
    """The message GitHub gave, for an error a person has to read."""
    try:
        payload = reply.json()
    except WireError:
        return reply.body[:200].decode("utf-8", "replace")
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return str(payload)[:200]


def workflow(branch: str = "main") -> str:
    """The pipeline that redeploys on a push and then checks the service.

    Both values are read from repository secrets rather than written into the
    file, so nothing secret is ever committed. The health check is what makes
    this a pipeline rather than a fire and forget hook.
    """
    return f"""name: Deploy

on:
  push:
    branches: [{branch}]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger the Render deploy
        run: curl --fail --silent --show-error -X POST "${{{{ secrets.{HOOK} }}}}"

      - name: Wait for the service to come back
        run: sleep 60

      - name: Check the deployed service is healthy
        run: |
          for attempt in 1 2 3 4 5 6; do
            if curl --fail --silent --show-error "${{{{ secrets.{APP_URL} }}}}/health"; then
              echo "the service is healthy"
              exit 0
            fi
            echo "not healthy yet, waiting"
            sleep 15
          done
          echo "the service did not become healthy"
          exit 1
"""


def app_url(root: Path) -> str | None:
    """The service URL module 6.5 stored, read back the same way."""
    values = projectstate.read_project_state(root)
    return values.get(projectstate.SERVICE_URL_KEY) or None


def wire(root: str | Path, hook: str, token: str | None = None,
         fetch: Fetch = send, repo: str | None = None,
         branch: str = "main") -> Wire:
    """Write the workflow, set both secrets, push it, and verify it is active.

    hook is the Render deploy hook URL, which Render publishes on the service's
    Settings tab and does not expose through its API.

    Never raises. A caller reads the result to decide what to tell a developer.
    """
    base = Path(root)
    name = repo or slug(base) or (base.name or str(base))

    if not base.is_dir():
        return Wire(name, False, detail=f"{base} is not a project directory")
    if not hook:
        return Wire(name, False, detail="no Render deploy hook was supplied")

    target = repo or slug(base)
    if not target:
        return Wire(name, False,
                    detail="the origin remote does not name a GitHub owner and repository")

    url = app_url(base)
    if not url:
        return Wire(target, False,
                    detail=f"no {projectstate.SERVICE_URL_KEY} in "
                           f"{projectstate.PROJECT_STATE_FILENAME}, run stage 5 first")

    try:
        actions = Actions(token=token, fetch=fetch)
    except WireError as exc:
        return Wire(target, False, detail=str(exc))

    # Written before the secrets are set so the push carries the finished file.
    path = base / WORKFLOW
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(workflow(branch), encoding="utf-8")
    except OSError as exc:
        return Wire(target, False, detail=f"cannot write {WORKFLOW}: {exc}")

    try:
        key, key_id = actions.public_key(target)
        actions.set_secret(target, HOOK, hook, key, key_id)
        actions.set_secret(target, APP_URL, url, key, key_id)
    except WireError as exc:
        return Wire(target, False, workflow=WORKFLOW, detail=str(exc))

    sent = push.run(base, token=actions.token, branch=branch)
    if not sent.ok:
        return Wire(target, False, secrets=(HOOK, APP_URL), workflow=WORKFLOW,
                    detail=f"the workflow could not be pushed: {sent.detail}")

    try:
        state = actions.state_of(target, WORKFLOW)
    except WireError as exc:
        return Wire(target, False, secrets=(HOOK, APP_URL), workflow=WORKFLOW,
                    pushed=True, detail=f"the pipeline could not be verified: {exc}")

    result = Wire(target, state == ACTIVE, secrets=(HOOK, APP_URL),
                  workflow=WORKFLOW, state=state, pushed=True,
                  detail="" if state == ACTIVE else f"the workflow is {state}, not active")
    logger.info("%s", result.summary())
    return result
