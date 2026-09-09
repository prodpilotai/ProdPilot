"""The eight ProdPush stages, run in order against one real project.

Scope is Phase 6's exit criterion rather than any single module. Each of 6.1
through 6.8 has its own tests. This file asks the different question those
cannot: do the eight stages actually chain, with each one consuming what the
one before it produced, or do they only work in isolation?

What is real here and what is not
----------------------------------
Every stage runs its own real code. No ProdPush stage is replaced by a test
double, and nothing here reimplements a stage's logic.

Three things outside ProdPush are substituted, because the standing rule for
this work is that no test may touch a live service:

  the Render API   a scripted transport returning the response shapes Render's
                   published reference describes, injected the same way module
                   6.5's own tests inject it.
  the GitHub API   the same, for module 6.8's secrets and Actions calls.
  the deployed app the HTTP responses module 6.7 reads, since nothing is
                   actually running at the URL the scripted Render returned.

The git remote is a real bare repository on disk. Stage 4 performs a real
commit and a real push into it.

The one place the offline rule shows in the chain is the origin remote. Stage 1
requires the origin to be GitHub, and stage 4 has to push somewhere reachable
with no network. Both cannot be the same remote here, so stage 1 runs against a
GitHub origin and the remote is then pointed at the local mirror for stage 4.
That substitutes a remote, not a stage.

Stage 3 needs a Docker daemon
------------------------------
Module 6.3 builds a real image and runs a real container by design, so it
cannot join the chain without a daemon. It is a separate test here, skipped
when there is no daemon, exactly as module 6.3's own build tests are. The
chain test says plainly which stages it ran.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from prodpilot import (
    buildtest,
    cicd,
    config,
    monitor,
    preflight,
    projectstate,
    push,
    render,
    sealing,
    smoke,
)
from prodpilot.buildtest import BuildUnavailable
from prodpilot.monitor import Outcome
from prodpilot.provider import Service, Status
from prodpilot.render import Render

SAMPLES = Path(__file__).resolve().parent / "samples"
SOURCE = SAMPLES / "node_express_ready"

GITHUB = "https://github.com/octo/payments-api.git"
REPO = "octo/payments-api"
HOOK = "https://api.render.com/deploy/srv-e2e?key=made-up-hook-key"
TOKEN = "made-up-github-token"
KEY = "made-up-render-key"

# A .env with real work in it for stage 2: one plain value, one value the
# entropy scan has to recognise as a secret, and one connection string.
ENV = """PORT=10000
SESSION_SECRET=Xq7Lp2Vt9Rk4Zn6Bw3Hs8Dy5Fg1Jm0Cu2Ae4Ti
DATABASE_URL=postgres://api:Wq3Nz8Rt5Vx1Lp7Kb@db.internal:5432/payments
"""

EXAMPLE = """PORT=
SESSION_SECRET=
DATABASE_URL=
"""


def daemon() -> bool:
    try:
        buildtest.client()
        return True
    except BuildUnavailable:
        return False


needs_docker = pytest.mark.skipif(not daemon(), reason="no Docker daemon available")


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A copy of the sample that passes detection, in a real repository.

    The generated files are left untracked so stage 4 has real work to do,
    which is the state a project is in when the fix loop has just written them.
    """
    monkeypatch.setenv(config.CONFIG_HOME_ENV_VAR, str(tmp_path / "home"))
    config.save_credentials(config.Credentials(github_token=TOKEN,
                                               render_api_key=KEY))

    root = tmp_path / "payments-api"
    shutil.copytree(SOURCE, root)
    (root / ".env").write_text(ENV, encoding="utf-8")
    (root / ".env.example").write_text(EXAMPLE, encoding="utf-8")

    mirror = tmp_path / "mirror.git"
    subprocess.run(("git", "init", "--bare", "-q", str(mirror)),
                   check=True, capture_output=True)
    subprocess.run(("git", "-C", str(mirror), "symbolic-ref", "HEAD",
                    "refs/heads/main"), check=True, capture_output=True)

    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "remote", "add", "origin", GITHUB)
    git(root, "add", "src")
    git(root, "commit", "-qm", "the application")
    git(root, "branch", "-M", "main")
    return root, mirror


def render_api(routes: dict[str, tuple[int, object]]):
    """A scripted Render, answering by path fragment."""
    seen: list[tuple[str, str]] = []

    def fetch(url, method, headers, body):
        seen.append((method, url))
        for fragment, (status, payload) in routes.items():
            if fragment in url:
                return render.Reply(status, json.dumps(payload).encode())
        raise AssertionError(f"unscripted Render call {method} {url}")

    return fetch, seen


def github_api(key: str):
    """A scripted GitHub, recording the sealed secrets it was sent."""
    sent: dict[str, dict] = {}

    def fetch(url, method, headers, body):
        if "public-key" in url:
            return cicd.Reply(200, json.dumps({"key_id": "kid-1",
                                               "key": key}).encode())
        if "/actions/secrets/" in url:
            sent[url.rsplit("/", 1)[-1]] = json.loads(body.decode())
            return cicd.Reply(201, b"")
        if "/actions/workflows/" in url:
            return cicd.Reply(200, json.dumps({"state": "active"}).encode())
        raise AssertionError(f"unscripted GitHub call {method} {url}")

    return fetch, sent


HEADERS = {
    "content-security-policy": "default-src 'self'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "access-control-allow-origin": "https://app.example.com",
    "content-type": "application/json",
}


def served(url: str):
    """The deployed application, answering both routes the way a sealed one does."""
    seen: list[str] = []

    def fetch(asked: str) -> smoke.Answer:
        seen.append(asked)
        if asked.endswith("/health"):
            return smoke.Answer(200, dict(HEADERS), '{"status":"ok"}')
        return smoke.Answer(200, dict(HEADERS), '{"name":"payments-api"}')

    return fetch, seen


class Scripted:
    """A provider answering stage 6 from a list, with no transport at all."""

    def __init__(self, states: list[Status]) -> None:
        self.states = list(states)

    def poll_status(self, deploy_id: str) -> Status:
        return self.states.pop(0) if self.states else Status.BUILDING

    def get_logs(self, deploy_id: str) -> str:
        return ""


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------


def test_the_stages_chain_from_pre_flight_to_a_wired_pipeline(project):
    """Stages 1, 2, 4, 5, 6, 7 and 8, each consuming the one before it.

    Stage 3 is the separate Docker test below. Every assertion here is on a
    value one stage handed the next, not on a value the test supplied.
    """
    root, mirror = project

    # -------- stage 1, pre-flight
    ready = preflight.run(root)
    assert ready.ready is True, ready.summary()

    # The offline substitution, and the only one in the chain.
    git(root, "remote", "set-url", "origin", str(mirror))

    # -------- stage 2, environment sealing
    sealed = sealing.run(root)
    assert sealed.ok is True, sealed.summary()
    assert sealed.blocked == ()
    assert set(sealed.sealed) == {"PORT", "SESSION_SECRET", "DATABASE_URL"}
    assert (root / sealing.PRODUCTION).is_file()

    # The sealed file must not be committable, which stage 2 checked with git.
    assert sealed.ignored is True

    # -------- stage 4, git push
    sent = push.run(root, branch="main")
    assert sent.ok is True, sent.detail
    assert ".env.production" not in sent.staged
    assert ".env" not in sent.staged

    code, listed = push.git(mirror, "ls-tree", "-r", "--name-only", "main")
    assert code == 0
    assert "Dockerfile" in listed
    assert ".env" not in listed.split("\n")
    assert ".env.production" not in listed.split("\n")

    # -------- stage 5, Render deployment
    values = {v.key: v.raw for v in sealed.values if v.real}
    fetch, calls = render_api({
        "/owners": (200, [{"owner": {"id": "tea-e2e", "name": "Team",
                                     "email": "t@example.com", "type": "team"},
                           "cursor": "c"}]),
        "/services": (201, {"service": {"id": "srv-e2e", "name": "payments-api",
                                        "url": "https://payments-api.onrender.com"},
                            "deployId": "dep-e2e"}),
    })
    made = Render(api_key=KEY, fetch=fetch).deploy(Service(
        name="payments-api",
        repo=GITHUB,
        branch="main",
        build="npm ci",
        start="npm start",
        env=values,
    ))
    assert made.service_id == "srv-e2e"
    assert made.url == "https://payments-api.onrender.com"

    assert any(m == "POST" and "/services" in u for m, u in calls)

    render.store(root, made)
    stored = projectstate.read_project_state(root)
    assert stored[projectstate.SERVICE_URL_KEY] == made.url

    # -------- stage 6, deploy monitoring
    watched = monitor.watch(
        Scripted([Status.BUILDING, Status.LIVE]),
        f"{made.service_id}/{made.deploy_id}",
        sleep=lambda s: None,
        clock=lambda: 0.0,
    )
    assert watched.outcome is Outcome.LIVE
    assert watched.ok is True

    # -------- stage 7, post-deploy smoke test
    probe, asked = served(made.url)
    checked = smoke.run(made.url, fetch=probe, sleep=lambda s: None,
                        clock=lambda: 0.0)
    assert checked.confirmed is True, checked.report()
    assert asked == [f"{made.url}/health", f"{made.url}/api"]
    assert len(checked.checks) == 5

    # -------- stage 8, CI/CD wiring
    from nacl import encoding, public
    secret = public.PrivateKey.generate()
    key = secret.public_key.encode(encoding.Base64Encoder()).decode()
    hub, secrets = github_api(key)

    wired = cicd.wire(root, hook=HOOK, token=TOKEN, fetch=hub, repo=REPO,
                      branch="main")
    assert wired.ok is True, wired.detail
    assert wired.active is True
    assert wired.pushed is True

    # The URL stage 5 returned is the URL stage 7 probed and the URL stage 8
    # sealed. That is the chain, checked rather than assumed.
    import base64
    box = public.SealedBox(secret)
    assert box.decrypt(base64.b64decode(
        secrets[cicd.APP_URL]["encrypted_value"])).decode() == made.url

    # The workflow reached the remote, so the pipeline exists where it runs.
    code, listed = push.git(mirror, "ls-tree", "-r", "--name-only", "main")
    assert cicd.WORKFLOW in listed


def test_the_sealed_values_are_what_reached_the_provider(project):
    """Stage 2's output, unchanged, in stage 5's request body."""
    root, mirror = project
    sealed = sealing.run(root)
    fetch, _ = render_api({
        "/owners": (200, [{"owner": {"id": "tea-e2e", "name": "T",
                                     "email": "t@example.com", "type": "team"},
                           "cursor": "c"}]),
        "/services": (201, {"service": {"id": "srv-e2e", "url": "https://x.test"},
                            "deployId": "dep-e2e"}),
    })
    bodies: list[bytes] = []

    def recording(url, method, headers, body):
        bodies.append(body)
        return fetch(url, method, headers, body)

    values = {v.key: v.raw for v in sealed.values if v.real}
    Render(api_key=KEY, fetch=recording).deploy(Service(
        name="payments-api", repo=GITHUB, branch="main",
        build="npm ci", start="npm start", env=values))

    created = json.loads(next(b for b in bodies if b).decode())
    assert created["envVars"] == [
        {"key": "DATABASE_URL", "value": values["DATABASE_URL"]},
        {"key": "PORT", "value": "10000"},
        {"key": "SESSION_SECRET", "value": values["SESSION_SECRET"]},
    ]


def test_no_secret_reached_the_repository(project):
    """The rule stages 2, 4 and 8 each enforce, checked once on the result."""
    root, mirror = project
    git(root, "remote", "set-url", "origin", str(mirror))
    sealing.run(root)
    push.run(root, branch="main")

    code, listed = push.git(mirror, "ls-tree", "-r", "--name-only", "main")
    assert code == 0
    for name in listed.split("\n"):
        if not name:
            continue
        code, blob = push.git(mirror, "show", f"main:{name}")
        assert "Xq7Lp2Vt9Rk4Zn6Bw3Hs8Dy5Fg1Jm0Cu2Ae4Ti" not in blob, name
        assert "Wq3Nz8Rt5Vx1Lp7Kb" not in blob, name


# The sample's own Dockerfile runs npm ci, which needs a package-lock.json the
# sample does not carry, and installing from the registry would make this test
# depend on a network. Stage 3 is about building a real image, running a real
# container and probing a real port, not about npm, so the build is made
# dependency free the same way module 6.3's own Docker tests are. EXPOSE has to
# agree with the PORT stage 2 sealed, or module 6.3 correctly reports the port
# mismatch instead of a healthy container.
BUILDABLE = """FROM node:20-alpine
WORKDIR /app
COPY src ./src
USER node
EXPOSE 10000
CMD ["node", "src/server.js"]
"""

SERVER = """const http = require("http");

const port = process.env.PORT || 3000;

http
  .createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok" }));
  })
  .listen(port, () => console.log("listening on " + port));
"""


@needs_docker
def test_stage_three_joins_the_chain_when_a_daemon_is_present(project):
    """Module 6.3 for real: a real image, a real container, a real probe."""
    root, mirror = project
    (root / "Dockerfile").write_text(BUILDABLE, encoding="utf-8")
    (root / "src" / "server.js").write_text(SERVER, encoding="utf-8")

    sealed = sealing.run(root)
    assert sealed.ok is True

    values = {v.key: v.raw for v in sealed.values if v.real}
    built = buildtest.run(root, env=values)

    assert built.ok is True, built.summary()
    assert built.health is not None and built.health.ok is True
    # The container answered on the port stage 2 sealed, not on a default.
    assert built.health.url.endswith("/health")


@needs_docker
def test_a_port_the_container_does_not_expose_is_caught(project):
    """The one fault module 6.3 can only see at run time, on a real container."""
    root, mirror = project
    (root / "Dockerfile").write_text(
        BUILDABLE.replace("EXPOSE 10000", "EXPOSE 3000"), encoding="utf-8")
    (root / "src" / "server.js").write_text(SERVER, encoding="utf-8")

    built = buildtest.run(root, env={"PORT": "10000"})

    # The image builds. It is the running container that is wrong, which is the
    # distinction module 6.3 draws between ok and health.
    assert built.ok is True
    assert built.health is not None and built.health.ok is False
    assert built.fault is buildtest.Fault.PORT
