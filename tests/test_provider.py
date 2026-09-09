"""Provider abstraction tests for Phase 6 module 6.9.

Module 6.9 is a verification module. It adds no product code. It asks one
question and answers it with running code rather than with an assertion on
paper: is a second deployment target a new implementation of four methods, or a
rewrite of stages 4 through 8?

The answer here is a second provider, Local, that shares nothing with Render.
No HTTP, no api key, no Render vocabulary, different identifier format, and a
deliberately different internal model. Every stage from 6.4 to 6.8 is then run
against it with its own real code and no edits, only a different object passed
in. Where a stage takes no provider at all, that is stated and checked too,
since a stage that never sees a provider is the strongest form of the same
claim.

What module 6.9 found
----------------------
Reading the tests that existed before this module, three of the four interface
methods were exercised properly against mocked network responses and one was
not:

  deploy       fully exercised. Happy path, the request body Section 7 names,
               sealed values injected through envVars, the no environment case,
               a response missing the identifiers, and an API error.
  poll_status  fully exercised. All eleven documented Render statuses, an
               undocumented status, the service/deploy addressing form, and a
               malformed identifier.
  get_logs     fully exercised. The query Render's logs endpoint takes, an
               empty result, and a read that raises, through the monitor.
  set_env      partially exercised. One happy path test with a single value,
               and nothing else. No ordering, no empty case, no error path.

set_env was also the least protected in a second way. The monitor's existing
test double offered poll_status and get_logs only, so before this module deploy
and set_env had never run against anything except Render. Both gaps are closed
below, the first with real error and ordering tests against mocked responses,
the second by Local implementing all four.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from prodpilot import cicd, monitor, projectstate, push, render, smoke
from prodpilot.buildtest import Fault
from prodpilot.monitor import Outcome, watch
from prodpilot.provider import METHODS, Deployment, Provider, Service, Status
from prodpilot.render import Render, RenderError

from tests.test_render import OWNERS, provider, reply


# --------------------------------------------------------------------------
# a second provider, sharing nothing with the first
# --------------------------------------------------------------------------


class Local:
    """A deployment target that is not Render and is not a network.

    Deliberately unlike Render in every way the interface does not fix. It
    keeps services in a dictionary, addresses a deploy by a bare identifier
    rather than Render's service/deploy pair, uses its own host name, and
    advances a deploy by a scripted sequence of states instead of polling
    anything. If a stage of ProdPush has quietly assumed a Render detail, this
    provider is where it shows.
    """

    host = "localhost.test"

    def __init__(self, states: list[Status] | None = None, logs: str = "") -> None:
        self.services: dict[str, dict] = {}
        self.states = list(states or [Status.LIVE])
        self.logs = logs
        self.polls = 0
        self.count = 0

    def deploy(self, service: Service) -> Deployment:
        self.count += 1
        service_id = f"local-{self.count}"
        self.services[service_id] = {
            "name": service.name,
            "repo": service.repo,
            "branch": service.branch,
            "build": service.build,
            "start": service.start,
            "env": dict(service.env),
        }
        return Deployment(
            service_id=service_id,
            deploy_id=f"d{self.count}",
            url=f"https://{service.name}.{self.host}",
        )

    def poll_status(self, deploy_id: str) -> Status:
        self.polls += 1
        return self.states.pop(0) if self.states else Status.BUILDING

    def get_logs(self, deploy_id: str) -> str:
        return self.logs

    def set_env(self, service_id: str, env) -> None:
        if service_id not in self.services:
            raise KeyError(f"no service {service_id}")
        self.services[service_id]["env"] = dict(env)


SERVICE = Service(
    name="api",
    repo="https://github.com/octo/api",
    branch="main",
    build="npm ci && npm run build",
    start="node dist/server.js",
    env={"NODE_ENV": "production", "PORT": "10000"},
)


# A real failing Docker build step, the same text module 6.3's taxonomy reads.
BUILD_LOG = (
    "Step 3/3 : RUN npm ci\n"
    "The command '/bin/sh -c npm ci' returned a non-zero code: 1"
)


def clocked():
    ticks = [0.0]
    return ticks, (lambda: ticks[0]), (lambda s: ticks.__setitem__(0, ticks[0] + s))


# --------------------------------------------------------------------------
# is Render a complete implementation of the interface
# --------------------------------------------------------------------------


def test_render_conforms_structurally():
    made, _ = provider()

    assert isinstance(made, Provider)


def test_render_implements_all_four_and_leaves_none_a_stub():
    """A method that only says ... would pass a callable check and do nothing."""
    made, _ = provider()

    for name in METHODS:
        method = getattr(made, name)
        assert callable(method), name
        body = inspect.getsource(method)
        assert body.count("\n") > 4, f"{name} looks like a stub"
        assert method.__doc__, f"{name} carries no docstring"


def test_every_method_keeps_the_interfaces_parameters_and_return_type():
    """The same names in the same order, returning the same thing."""
    for name in METHODS:
        wanted = inspect.signature(getattr(Provider, name))
        actual = inspect.signature(getattr(Render, name))
        assert list(actual.parameters) == list(wanted.parameters), name
        assert actual.return_annotation == wanted.return_annotation, name


def test_the_second_provider_conforms_by_the_same_measure():
    made = Local()

    assert isinstance(made, Provider)
    for name in METHODS:
        wanted = inspect.signature(getattr(Provider, name))
        actual = inspect.signature(getattr(made, name))
        assert list(actual.parameters) == list(wanted.parameters)[1:], name


def test_the_second_provider_borrows_nothing_from_the_first():
    """Otherwise it would prove only that Render works against Render.

    Checked structurally rather than by reading the source for words, since
    prose about Render is not a dependency on Render.
    """
    assert Local.__bases__ == (object,)
    assert Render not in Local.__mro__
    assert Provider not in Local.__mro__, "it conforms by shape, not by declaring so"

    shared = {n for n in set(dir(Local)) & set(dir(Render))
              if not n.startswith("__")}
    assert shared == set(METHODS), shared


def test_no_stage_of_prodpush_imports_the_render_module():
    """The architectural claim, checked against the source rather than asserted.

    If a stage imported render, adding a provider would mean editing that
    stage. Only render.py may name Render.
    """
    root = Path(render.__file__).parent
    guilty = []
    for path in sorted(root.glob("*.py")):
        if path.name == "render.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "render" in stripped:
                guilty.append(f"{path.name}: {stripped}")

    assert guilty == [], guilty


# --------------------------------------------------------------------------
# closing the gap module 6.9 found: set_env, against mocked responses
#
# These are Render tests and they live here because module 6.9 is what found
# the hole. Moving them would lose the record of why they exist.
# --------------------------------------------------------------------------


def test_set_env_sends_every_value_in_a_stable_order():
    """Sorted, so a re-deploy with the same values makes the same request."""
    made, api = provider({"/owners": reply(200, OWNERS),
                          "/env-vars": reply(200, [])})

    made.set_env("srv-abc", {"PORT": "10000", "NODE_ENV": "production",
                             "DATABASE_URL": "postgres://host/db"})

    assert api.body_of("/env-vars") == [
        {"key": "DATABASE_URL", "value": "postgres://host/db"},
        {"key": "NODE_ENV", "value": "production"},
        {"key": "PORT", "value": "10000"},
    ]


def test_set_env_addresses_the_service_it_was_given():
    made, api = provider({"/owners": reply(200, OWNERS),
                          "/env-vars": reply(200, [])})

    made.set_env("srv-zzz", {"PORT": "10000"})

    url = next(url for _, url, _ in api.calls if "env-vars" in url)
    assert "/services/srv-zzz/env-vars" in url


def test_set_env_uses_put_because_it_replaces_rather_than_appends():
    made, api = provider({"/owners": reply(200, OWNERS),
                          "/env-vars": reply(200, [])})

    made.set_env("srv-abc", {"PORT": "10000"})

    method = next(m for m, url, _ in api.calls if "env-vars" in url)
    assert method == "PUT"


def test_set_env_with_nothing_to_set_still_sends_a_valid_request():
    made, api = provider({"/owners": reply(200, OWNERS),
                          "/env-vars": reply(200, [])})

    made.set_env("srv-abc", {})

    assert api.body_of("/env-vars") == []


def test_a_rejected_set_env_raises_with_renders_own_words():
    """The gap that mattered most. Before this, no failure path was covered."""
    made, _ = provider({"/owners": reply(200, OWNERS), "/env-vars": reply(
        400, {"message": "envVars.0.key must not be empty"})})

    with pytest.raises(RenderError) as caught:
        made.set_env("srv-abc", {"PORT": "10000"})

    assert "must not be empty" in str(caught.value)
    assert "400" in str(caught.value)


def test_set_env_on_a_service_that_does_not_exist_raises():
    made, _ = provider({"/owners": reply(200, OWNERS),
                        "/env-vars": reply(404, {"message": "Not Found"})})

    with pytest.raises(RenderError):
        made.set_env("srv-gone", {"PORT": "10000"})


def test_a_secret_value_never_reaches_the_error_text():
    made, _ = provider({"/owners": reply(200, OWNERS),
                        "/env-vars": reply(500, {"message": "server error"})})

    with pytest.raises(RenderError) as caught:
        made.set_env("srv-abc", {"DATABASE_URL": "postgres://user:hunter2@host/db"})

    assert "hunter2" not in str(caught.value)


# --------------------------------------------------------------------------
# stage 5 against the second provider
# --------------------------------------------------------------------------


def test_the_second_provider_deploys_and_returns_the_three_identifiers():
    made = Local()

    result = made.deploy(SERVICE)

    assert isinstance(result, Deployment)
    assert result.service_id and result.deploy_id and result.url


def test_the_environment_travels_through_the_second_provider_too():
    made = Local()

    result = made.deploy(SERVICE)

    assert made.services[result.service_id]["env"] == dict(SERVICE.env)


def test_module_6_5s_storage_needs_no_change_for_a_second_provider(tmp_path: Path):
    """store takes a Deployment, not a Render, so it already works."""
    made = Local()

    result = made.deploy(SERVICE)
    render.store(tmp_path, result)

    values = projectstate.read_project_state(tmp_path)
    assert values[projectstate.SERVICE_ID_KEY] == result.service_id
    assert values[projectstate.DEPLOY_ID_KEY] == result.deploy_id
    assert values[projectstate.SERVICE_URL_KEY] == "https://api.localhost.test"


def test_set_env_on_the_second_provider_replaces_the_values():
    """The fourth method, now exercised against something other than Render."""
    made = Local()
    result = made.deploy(SERVICE)

    made.set_env(result.service_id, {"PORT": "8080"})

    assert made.services[result.service_id]["env"] == {"PORT": "8080"}


# --------------------------------------------------------------------------
# stage 6 against the second provider
# --------------------------------------------------------------------------


def test_the_monitor_watches_the_second_provider_unchanged():
    made = Local([Status.BUILDING, Status.BUILDING, Status.LIVE])
    ticks, clock, sleep = clocked()

    result = watch(made, "d1", sleep=sleep, clock=clock)

    assert result.outcome is Outcome.LIVE
    assert result.polls == 3


def test_a_failure_on_the_second_provider_is_classified_the_same_way():
    """Module 6.3's taxonomy is about build output, not about a platform."""
    made = Local([Status.FAILED], logs=BUILD_LOG)
    ticks, clock, sleep = clocked()

    result = watch(made, "d1", sleep=sleep, clock=clock)

    assert result.outcome is Outcome.FAILED
    assert result.fault is Fault.DEPENDENCY
    assert result.actionable is True
    assert result.review is False


def test_the_second_providers_deploy_id_format_is_not_a_problem():
    """Render addresses a deploy as service/deploy. Local does not. Neither
    the monitor nor anything downstream may care."""
    made = Local([Status.LIVE])
    ticks, clock, sleep = clocked()

    result = watch(made, "d1", sleep=sleep, clock=clock)

    assert result.deploy_id == "d1"
    assert result.ok is True


def test_a_second_provider_that_never_finishes_still_times_out():
    made = Local([Status.BUILDING] * 100)
    ticks, clock, sleep = clocked()

    result = watch(made, "d1", sleep=sleep, clock=clock)

    assert result.outcome is Outcome.TIMEOUT
    assert ticks[0] >= monitor.LIMIT


# --------------------------------------------------------------------------
# stages 4, 7 and 8 take no provider at all
# --------------------------------------------------------------------------


def test_stage_four_and_stage_seven_and_stage_eight_take_no_provider():
    """The strongest form of the claim: they cannot depend on one."""
    for function in (push.run, smoke.run, cicd.wire):
        names = list(inspect.signature(function).parameters)
        assert "provider" not in names, function.__name__


def test_the_smoke_test_runs_against_the_second_providers_url():
    """Stage 7 reads a URL over HTTP. Which platform produced it is invisible."""
    made = Local()
    result = made.deploy(SERVICE)
    seen: list[str] = []

    def fetch(url: str) -> smoke.Answer:
        seen.append(url)
        return smoke.Answer(200, dict(GOOD), '{"ok":true}')

    checked = smoke.run(result.url, fetch=fetch, sleep=lambda s: None,
                        clock=lambda: 0.0)

    assert checked.confirmed is True
    assert seen == ["https://api.localhost.test/health",
                    "https://api.localhost.test/api"]


GOOD = {
    "content-security-policy": "default-src 'self'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "access-control-allow-origin": "https://app.example.com",
    "content-type": "application/json",
}


# --------------------------------------------------------------------------
# stage 8 reads what any provider stored, end to end
# --------------------------------------------------------------------------


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", "-q", str(remote)),
                   check=True, capture_output=True)
    subprocess.run(("git", "-C", str(remote), "symbolic-ref", "HEAD",
                    "refs/heads/main"), check=True, capture_output=True)
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "remote", "add", "origin", str(remote))
    (root / ".gitignore").write_text("node_modules\n.env\n", encoding="utf-8")
    (root / "README.md").write_text("start\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    git(root, "branch", "-M", "main")
    git(root, "push", "-q", "origin", "main")
    return root


def test_stage_eight_wires_a_pipeline_for_a_service_the_second_provider_made(repo: Path):
    """Stage 5 with Local, then stage 8's real code, unedited, on top of it."""
    made = Local()
    result = made.deploy(SERVICE)
    render.store(repo, result)

    from nacl import encoding, public
    secret = public.PrivateKey.generate()
    key = secret.public_key.encode(encoding.Base64Encoder()).decode()
    sent: dict[str, dict] = {}

    def fetch(url, method, headers, body):
        if "public-key" in url:
            return cicd.Reply(200, json.dumps(
                {"key_id": "kid-1", "key": key}).encode())
        if "/actions/secrets/" in url:
            sent[url.rsplit("/", 1)[-1]] = json.loads(body.decode())
            return cicd.Reply(201, b"")
        return cicd.Reply(200, json.dumps({"state": "active"}).encode())

    wired = cicd.wire(repo, hook="https://hook.example/deploy", token="t",
                      fetch=fetch, repo="octo/api")

    assert wired.ok is True
    box = public.SealedBox(secret)
    import base64
    assert box.decrypt(base64.b64decode(
        sent[cicd.APP_URL]["encrypted_value"])).decode() == "https://api.localhost.test"


def test_nothing_in_this_file_reached_a_network():
    """Local has no transport, and every Render call here is a scripted reply.

    Read from the import lines rather than from the whole file, so the check
    cannot be satisfied or broken by its own text.
    """
    imports = [line.strip() for line
               in Path(__file__).read_text(encoding="utf-8").splitlines()
               if line.startswith(("import ", "from "))]

    for line in imports:
        for name in ("urllib", "socket", "http", "requests"):
            assert name not in line, line

    for made in (Local(), provider()[0]):
        assert getattr(made, "fetch", None) not in (render.send, cicd.send)
