"""Pre-flight and provider interface tests for Phase 6 module 6.1.

The four checks are independent, so each is driven passing and failing on its
own, and then together, against real Git repositories created in a temporary
directory rather than mocked.

The credential checks read module 1.3's store, so they are pointed at a
temporary config home rather than the developer's real one. No test reads or
writes a real credential.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from prodpilot import config, preflight, provider
from prodpilot.preflight import (
    GITHUB_TOKEN,
    HOSTS,
    ORIGIN,
    RENDER_KEY,
    REPO,
    SETUP,
    host_of,
    run,
)
from prodpilot.provider import METHODS, Deployment, Provider, Service, Status


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


def repo(root: Path, origin: str | None = "https://github.com/octo/api.git") -> Path:
    """A real Git repository, optionally with a remote."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    if origin:
        git(root, "remote", "add", "origin", origin)
    return root


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    """Point module 1.3's store at a temporary home, never the real one."""
    monkeypatch.setenv(config.CONFIG_HOME_ENV_VAR, str(tmp_path / "home"))
    return tmp_path / "home"


def save(github: str | None, render: str | None) -> None:
    config.save_credentials(config.Credentials(github_token=github, render_api_key=render))


# --------------------------------------------------------------------------
# the repository check
# --------------------------------------------------------------------------


def test_a_git_repository_passes(tmp_path: Path, store):
    save("t", "r")

    result = run(repo(tmp_path / "project"))

    assert result.get(REPO).ok is True
    assert "Git repository" in result.get(REPO).detail


def test_a_directory_that_is_not_a_repository_fails(tmp_path: Path, store):
    save("t", "r")
    plain = tmp_path / "plain"
    plain.mkdir()

    check = run(plain).get(REPO)

    assert check.ok is False
    assert "not a Git repository" in check.detail
    assert "git init" in check.fix


def test_a_path_that_does_not_exist_fails(tmp_path: Path, store):
    save("t", "r")

    check = run(tmp_path / "nowhere").get(REPO)

    assert check.ok is False
    assert "does not exist" in check.detail
    assert check.fix


def test_a_file_is_not_a_project(tmp_path: Path, store):
    save("t", "r")
    target = tmp_path / "a-file"
    target.write_text("hello\n", encoding="utf-8")

    check = run(target).get(REPO)

    assert check.ok is False
    assert "not a directory" in check.detail


# --------------------------------------------------------------------------
# the origin check
# --------------------------------------------------------------------------


def test_a_github_https_origin_passes(tmp_path: Path, store):
    save("t", "r")

    check = run(repo(tmp_path / "project")).get(ORIGIN)

    assert check.ok is True
    assert "github.com" in check.detail


def test_a_github_ssh_origin_passes(tmp_path: Path, store):
    """Git accepts the scp style remote, so pre-flight has to as well."""
    save("t", "r")

    check = run(repo(tmp_path / "project", "git@github.com:octo/api.git")).get(ORIGIN)

    assert check.ok is True


def test_an_origin_somewhere_other_than_github_fails(tmp_path: Path, store):
    save("t", "r")

    check = run(repo(tmp_path / "project", "https://gitlab.com/octo/api.git")).get(ORIGIN)

    assert check.ok is False
    assert "gitlab.com" in check.detail
    assert "GitHub" in check.fix


def test_a_repository_with_no_origin_fails(tmp_path: Path, store):
    save("t", "r")

    check = run(repo(tmp_path / "project", origin=None)).get(ORIGIN)

    assert check.ok is False
    assert "no remote named origin" in check.detail
    assert "git remote add origin" in check.fix


def test_the_origin_check_says_so_when_there_is_no_repository(tmp_path: Path, store):
    """Rather than reporting a missing remote, which would be misleading."""
    save("t", "r")
    plain = tmp_path / "plain"
    plain.mkdir()

    check = run(plain).get(ORIGIN)

    assert check.ok is False
    assert "no repository" in check.detail


@pytest.mark.parametrize(
    "remote,host",
    [
        ("https://github.com/o/r.git", "github.com"),
        ("http://github.com/o/r", "github.com"),
        ("git@github.com:o/r.git", "github.com"),
        ("ssh://git@github.com/o/r.git", "github.com"),
        ("https://GITHUB.com/o/r.git", "github.com"),
        ("https://gitlab.com/o/r.git", "gitlab.com"),
        ("https://bitbucket.org/o/r.git", "bitbucket.org"),
    ],
)
def test_the_host_is_read_from_either_remote_form(remote: str, host: str):
    assert host_of(remote) == host


@pytest.mark.parametrize("remote", ["", "   ", "not a url", "/local/path"])
def test_something_that_is_not_a_remote_url_has_no_host(remote: str):
    assert host_of(remote) is None


def test_the_accepted_hosts_are_github_only():
    assert all("github.com" in host for host in HOSTS)


# --------------------------------------------------------------------------
# the two credential checks, read through module 1.3
# --------------------------------------------------------------------------


def test_both_credentials_present_pass(tmp_path: Path, store):
    save("a-token", "a-key")

    result = run(repo(tmp_path / "project"))

    assert result.get(RENDER_KEY).ok is True
    assert result.get(GITHUB_TOKEN).ok is True


def test_a_missing_render_key_fails_on_its_own(tmp_path: Path, store):
    save("a-token", None)

    result = run(repo(tmp_path / "project"))

    assert result.get(RENDER_KEY).ok is False
    assert result.get(GITHUB_TOKEN).ok is True
    assert SETUP in result.get(RENDER_KEY).fix


def test_a_missing_github_token_fails_on_its_own(tmp_path: Path, store):
    save(None, "a-key")

    result = run(repo(tmp_path / "project"))

    assert result.get(GITHUB_TOKEN).ok is False
    assert result.get(RENDER_KEY).ok is True
    assert SETUP in result.get(GITHUB_TOKEN).fix


def test_an_empty_store_fails_both(tmp_path: Path, store):
    result = run(repo(tmp_path / "project"))

    assert result.get(RENDER_KEY).ok is False
    assert result.get(GITHUB_TOKEN).ok is False


def test_an_unreadable_store_is_not_a_crash(tmp_path: Path, store):
    """A damaged config file must not stop pre-flight reporting."""
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not valid toml = = =", encoding="utf-8")

    result = run(repo(tmp_path / "project"))

    assert result.get(RENDER_KEY).ok is False
    assert "could not be read" in result.get(RENDER_KEY).detail
    assert SETUP in result.get(GITHUB_TOKEN).fix


def test_no_secret_value_is_ever_reported(tmp_path: Path, store):
    """Pre-flight asks whether a credential is present, never what it is."""
    save("ghp_supersecrettokenvalue", "rnd_supersecretkeyvalue")

    payload = json.dumps(run(repo(tmp_path / "project")).to_dict())

    assert "supersecret" not in payload


def test_the_fix_points_at_module_1_3_rather_than_a_new_prompt():
    """Section 7 says missing items are collected by the existing setup flow."""
    assert SETUP == "prodpilot setup"
    source = Path(preflight.__file__).read_text(encoding="utf-8")
    for word in ("getpass", "input(", "typer.prompt", "save_credentials"):
        assert word not in source, f"{word} means a second collection path"


# --------------------------------------------------------------------------
# the combined result
# --------------------------------------------------------------------------


def test_all_four_checks_run_every_time(tmp_path: Path, store):
    save("t", "r")

    result = run(repo(tmp_path / "project"))

    assert [c.name for c in result.checks] == [REPO, ORIGIN, RENDER_KEY, GITHUB_TOKEN]
    assert result.ready is True
    assert result.failed == ()


def test_everything_failing_at_once_is_reported_at_once(tmp_path: Path, store):
    """A developer should be able to fix all of it in one pass."""
    plain = tmp_path / "plain"
    plain.mkdir()

    result = run(plain)

    assert result.ready is False
    assert len(result.failed) == 4
    assert {c.name for c in result.failed} == {REPO, ORIGIN, RENDER_KEY, GITHUB_TOKEN}
    for check in result.failed:
        assert check.detail.strip()
        assert check.fix.strip()


def test_a_partial_failure_names_only_what_failed(tmp_path: Path, store):
    save("t", None)

    result = run(repo(tmp_path / "project", "https://gitlab.com/o/r.git"))

    assert {c.name for c in result.failed} == {ORIGIN, RENDER_KEY}
    assert result.ready is False


def test_the_credential_checks_do_not_depend_on_the_project(tmp_path: Path, store):
    """Whether a token is stored has nothing to do with the project path."""
    save("t", "r")

    missing = run(tmp_path / "nowhere")

    assert missing.get(RENDER_KEY).ok is True
    assert missing.get(GITHUB_TOKEN).ok is True


def test_every_failed_check_says_what_to_do(tmp_path: Path, store):
    """Stage 2 acts on a failure without working out again what went wrong."""
    plain = tmp_path / "plain"
    plain.mkdir()

    for check in run(plain).failed:
        assert check.fix, check.name


def test_the_result_serialises_whole(tmp_path: Path, store):
    save("t", "r")

    payload = run(repo(tmp_path / "project")).to_dict()

    assert set(payload) == {"project", "ready", "checks", "failed"}
    assert len(payload["checks"]) == 4
    json.dumps(payload)


def test_asking_for_a_check_that_does_not_exist_is_refused(tmp_path: Path, store):
    with pytest.raises(KeyError):
        run(tmp_path).get("nonsense")


def test_the_summary_reads_for_a_terminal(tmp_path: Path, store):
    save("t", "r")
    ready = run(repo(tmp_path / "project"))

    assert "ready for deployment" in ready.summary()
    assert "not ready" in run(tmp_path / "nowhere").summary()


def test_a_missing_git_binary_is_reported_not_raised(tmp_path: Path, store, monkeypatch):
    """Git may simply not be installed on the developer's machine."""
    def absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(preflight.subprocess, "run", absent)
    code, output = preflight.git(tmp_path, "remote", "get-url", "origin")

    assert code == 1
    assert "not installed" in output


def test_a_hanging_git_command_is_reported_not_raised(tmp_path: Path, store, monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=15)

    monkeypatch.setattr(preflight.subprocess, "run", hang)
    code, output = preflight.git(tmp_path, "remote", "get-url", "origin")

    assert code == 1
    assert "did not respond" in output


# --------------------------------------------------------------------------
# the provider interface, shaped now so 6.5 and 6.6 do not rewrite it
# --------------------------------------------------------------------------


def test_the_interface_has_exactly_the_four_methods_the_document_names():
    """Section 7.2: deploy, poll_status, get_logs, set_env."""
    assert METHODS == ("deploy", "poll_status", "get_logs", "set_env")

    declared = {n for n in vars(Provider) if not n.startswith("_")}
    assert declared == set(METHODS)


def test_nothing_implements_the_interface_yet():
    """The Render implementation is 6.5 and 6.6, not this module."""
    source = Path(provider.__file__).read_text(encoding="utf-8").lower()
    for word in ("requests", "http", "render.com", "api_key", "urllib"):
        assert word not in source, word


def test_an_object_with_the_four_methods_conforms():
    """Structural, so an implementation needs no import from the interface."""

    class Fake:
        def deploy(self, service): ...
        def poll_status(self, deploy_id): ...
        def get_logs(self, deploy_id): ...
        def set_env(self, service_id, env): ...

    assert isinstance(Fake(), Provider)


def test_an_object_missing_a_method_does_not_conform():
    class Partial:
        def deploy(self, service): ...
        def poll_status(self, deploy_id): ...

    assert not isinstance(Partial(), Provider)


@pytest.mark.parametrize(
    "method,params",
    [
        ("deploy", ["self", "service"]),
        ("poll_status", ["self", "deploy_id"]),
        ("get_logs", ["self", "deploy_id"]),
        ("set_env", ["self", "service_id", "env"]),
    ],
)
def test_each_method_has_the_signature_later_modules_will_implement(method, params):
    """isinstance against a Protocol only checks names, so signatures are
    checked here rather than assumed."""
    assert list(inspect.signature(getattr(Provider, method)).parameters) == params


def test_the_records_carry_what_section_seven_says_they_carry():
    service = Service(name="api", repo="https://github.com/o/r.git", branch="main",
                      build="npm ci", start="npm start", env={"PORT": "3000"})
    made = Deployment(service_id="srv-1", deploy_id="dep-1", url="https://api.onrender.com")

    assert service.env["PORT"] == "3000"
    assert (made.service_id, made.deploy_id, made.url) == (
        "srv-1", "dep-1", "https://api.onrender.com")


def test_a_service_needs_no_environment_to_be_described():
    service = Service(name="api", repo="r", branch="main", build="b", start="s")

    assert dict(service.env) == {}


def test_the_three_deploy_states_are_the_ones_stage_six_acts_on():
    assert {s.value for s in Status} == {"building", "live", "failed"}


def test_the_records_are_frozen():
    service = Service(name="api", repo="r", branch="main", build="b", start="s")

    with pytest.raises(Exception):
        service.name = "other"
