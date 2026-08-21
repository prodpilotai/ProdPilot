"""Config and secrets tests for Phase 1 module 1.3.

The CLI commands are exercised by spawning the real command as a subprocess,
which is the command line equivalent of the real client over stdio approach the
1.1 and 1.2 tests use. Nothing here imports a command function and calls it
directly, because that would not prove the installed entry point works.

Every test points PRODPILOT_CONFIG_HOME at a scratch directory so no test can
read or overwrite the developer's real credentials.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from prodpilot import config as config_module
from prodpilot import projectstate
from prodpilot.config import (
    Credentials,
    PermissionState,
    load_credentials,
    save_credentials,
    verify_permissions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

TEST_GITHUB_TOKEN = "ghp_unittest000000000000000000000000"
TEST_RENDER_KEY = "rnd_unittest111111111111111111111111"


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the config store at a scratch directory for the duration of a test."""
    monkeypatch.setenv(config_module.CONFIG_HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def run_cli(args: list[str], config_home_path: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run the real CLI as a subprocess against a scratch config home."""
    env = dict(os.environ)
    env[config_module.CONFIG_HOME_ENV_VAR] = str(config_home_path)
    return subprocess.run(
        [sys.executable, "-m", "prodpilot", *args],
        cwd=str(REPO_ROOT),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------------
# Credential store
# --------------------------------------------------------------------------


def test_config_path_is_under_the_config_home(config_home: Path):
    assert config_module.config_dir() == config_home / ".prodpilot"
    assert config_module.config_path().name == "config.toml"


def test_credentials_round_trip(config_home: Path):
    save_credentials(Credentials(TEST_GITHUB_TOKEN, TEST_RENDER_KEY))

    loaded = load_credentials()

    assert loaded.github_token == TEST_GITHUB_TOKEN
    assert loaded.render_api_key == TEST_RENDER_KEY
    assert loaded.is_complete
    assert loaded.missing == ()


def test_missing_config_returns_empty_credentials(config_home: Path):
    loaded = load_credentials()

    assert not loaded.is_complete
    assert set(loaded.missing) == set(config_module.REQUIRED_CREDENTIALS)


def test_stored_file_is_valid_toml_under_a_credentials_table(config_home: Path):
    save_credentials(Credentials(TEST_GITHUB_TOKEN, TEST_RENDER_KEY))

    with config_module.config_path().open("rb") as handle:
        document = tomllib.load(handle)

    assert document["credentials"]["github_token"] == TEST_GITHUB_TOKEN
    assert document["credentials"]["render_api_key"] == TEST_RENDER_KEY


def test_saving_preserves_unrelated_settings(config_home: Path):
    """A rerun of setup must not destroy other config added later."""
    path = config_module.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[ui]\ncolour = "always"\n', encoding="utf-8")

    save_credentials(Credentials(TEST_GITHUB_TOKEN, TEST_RENDER_KEY))

    with path.open("rb") as handle:
        document = tomllib.load(handle)
    assert document["ui"]["colour"] == "always"
    assert document["credentials"]["github_token"] == TEST_GITHUB_TOKEN


def test_saved_file_is_restricted_to_the_current_user(config_home: Path):
    """The whole point of the store. Verified by reading the real state back."""
    report = save_credentials(Credentials(TEST_GITHUB_TOKEN, TEST_RENDER_KEY))

    assert report.state is PermissionState.RESTRICTED, report.detail
    assert verify_permissions(config_module.config_path()).is_restricted


def test_permission_report_on_a_missing_file_is_unknown(config_home: Path):
    report = verify_permissions(config_home / "nope.toml")

    assert report.state is PermissionState.UNKNOWN


# --------------------------------------------------------------------------
# Project state conventions
# --------------------------------------------------------------------------


def test_project_state_round_trip(tmp_path: Path):
    projectstate.write_project_state(
        tmp_path,
        {
            projectstate.SERVICE_ID_KEY: "srv-abc123",
            projectstate.SERVICE_URL_KEY: "https://example.onrender.com",
        },
    )

    state = projectstate.read_project_state(tmp_path)

    assert state[projectstate.SERVICE_ID_KEY] == "srv-abc123"
    assert state[projectstate.SERVICE_URL_KEY] == "https://example.onrender.com"


def test_project_state_merges_rather_than_replaces(tmp_path: Path):
    projectstate.write_project_state(tmp_path, {projectstate.SERVICE_ID_KEY: "srv-1"})
    projectstate.write_project_state(tmp_path, {projectstate.DEPLOY_ID_KEY: "dep-1"})

    state = projectstate.read_project_state(tmp_path)

    assert state[projectstate.SERVICE_ID_KEY] == "srv-1"
    assert state[projectstate.DEPLOY_ID_KEY] == "dep-1"


def test_missing_state_file_reads_as_empty(tmp_path: Path):
    assert projectstate.read_project_state(tmp_path) == {}


@pytest.mark.parametrize(
    "key",
    [
        "GITHUB_TOKEN",
        "RENDER_API_KEY",
        "MY_SECRET",
        "DB_PASSWORD",
        "some_private_key",
        "SERVICE_CREDENTIAL",
    ],
)
def test_credential_keys_are_refused_in_a_project_directory(tmp_path: Path, key: str):
    """Section 2.1 forbids writing a secret into a repository. Fail closed."""
    with pytest.raises(projectstate.CredentialInProjectError):
        projectstate.write_project_state(tmp_path, {key: "value"})

    assert not projectstate.state_path(tmp_path).exists()


def test_malformed_keys_are_refused(tmp_path: Path):
    with pytest.raises(projectstate.ProjectStateError):
        projectstate.write_project_state(tmp_path, {"not a key": "value"})


def test_comments_and_blank_lines_are_ignored_when_reading(tmp_path: Path):
    projectstate.state_path(tmp_path).write_text(
        "# a comment\n\nPRODPILOT_SERVICE_ID=srv-9\nnot an assignment\n",
        encoding="utf-8",
    )

    state = projectstate.read_project_state(tmp_path)

    assert state == {"PRODPILOT_SERVICE_ID": "srv-9"}


def test_gitignore_status_reports_when_not_excluded(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")

    status = projectstate.check_gitignored(tmp_path)

    assert status.gitignore_exists
    assert not status.is_ignored


def test_ensure_gitignored_adds_the_entry(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")

    status = projectstate.ensure_gitignored(tmp_path)

    assert status.is_ignored
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert projectstate.PROJECT_STATE_FILENAME in body


def test_ensure_gitignored_creates_the_file_when_absent(tmp_path: Path):
    status = projectstate.ensure_gitignored(tmp_path)

    assert status.is_ignored
    assert (tmp_path / ".gitignore").is_file()


def test_a_wildcard_env_pattern_counts_as_excluded(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(".env*\n", encoding="utf-8")

    assert projectstate.check_gitignored(tmp_path).is_ignored


# --------------------------------------------------------------------------
# CLI, run as a real subprocess
# --------------------------------------------------------------------------


def test_doctor_fails_when_nothing_is_configured(tmp_path: Path):
    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 1
    assert "not found" in result.stdout
    assert "prodpilot setup" in result.stdout


def test_setup_stores_both_credentials(tmp_path: Path):
    result = run_cli(
        ["setup"], tmp_path, stdin=f"{TEST_GITHUB_TOKEN}\n{TEST_RENDER_KEY}\n"
    )

    assert result.returncode == 0, result.stderr
    stored = tmp_path / ".prodpilot" / "config.toml"
    assert stored.is_file()
    with stored.open("rb") as handle:
        document = tomllib.load(handle)
    assert document["credentials"]["github_token"] == TEST_GITHUB_TOKEN


def test_setup_never_echoes_the_secret_values(tmp_path: Path):
    """Nothing entered may be printed back, on either stream."""
    result = run_cli(
        ["setup"], tmp_path, stdin=f"{TEST_GITHUB_TOKEN}\n{TEST_RENDER_KEY}\n"
    )

    assert TEST_GITHUB_TOKEN not in result.stdout
    assert TEST_GITHUB_TOKEN not in result.stderr
    assert TEST_RENDER_KEY not in result.stdout
    assert TEST_RENDER_KEY not in result.stderr


def test_setup_refuses_an_empty_credential(tmp_path: Path):
    """An empty answer must fail rather than store a blank credential."""
    result = run_cli(["setup"], tmp_path, stdin="\n")

    assert result.returncode == 1
    assert not (tmp_path / ".prodpilot" / "config.toml").exists()


def test_doctor_passes_after_setup(tmp_path: Path):
    run_cli(["setup"], tmp_path, stdin=f"{TEST_GITHUB_TOKEN}\n{TEST_RENDER_KEY}\n")

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout


def test_doctor_never_prints_stored_secret_values(tmp_path: Path):
    run_cli(["setup"], tmp_path, stdin=f"{TEST_GITHUB_TOKEN}\n{TEST_RENDER_KEY}\n")

    result = run_cli(["doctor"], tmp_path)

    assert TEST_GITHUB_TOKEN not in result.stdout
    assert TEST_RENDER_KEY not in result.stdout
    assert "stored" in result.stdout


def test_doctor_reports_a_project_that_is_not_gitignored(tmp_path: Path, monkeypatch):
    run_cli(["setup"], tmp_path, stdin=f"{TEST_GITHUB_TOKEN}\n{TEST_RENDER_KEY}\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules\n", encoding="utf-8")

    result = run_cli(["doctor", "--project", str(project)], tmp_path)

    assert result.returncode == 1
    assert "not excluded" in result.stdout


def test_setup_writes_nothing_into_the_working_directory(tmp_path: Path):
    """The credential store must never appear inside a project tree."""
    project = tmp_path / "project"
    project.mkdir()
    before = {p.name for p in project.iterdir()}

    run_cli(["setup"], tmp_path, stdin=f"{TEST_GITHUB_TOKEN}\n{TEST_RENDER_KEY}\n")

    assert {p.name for p in project.iterdir()} == before
    assert not (REPO_ROOT / "config.toml").exists()
    assert not (REPO_ROOT / ".prodpilot").exists()
