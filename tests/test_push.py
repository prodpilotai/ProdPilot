"""Git push tests for Phase 6 module 6.4.

Every push here goes to a real local bare repository. Nothing in this file
touches a real GitHub remote or the network, and the token used is a made up
string that no server ever sees.

The load bearing test is the one that proves .env.production is never staged.
Section 2.1 principle 5 is the reason module 6.2 exists, and a push that
committed the sealed file would undo it in one command.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prodpilot import push
from prodpilot.push import (
    GENERATED,
    MESSAGE,
    USER,
    Fail,
    Push,
    authed,
    classify,
    https_url,
    local,
    origin_url,
    pending,
    run,
    scrub,
    target,
)

TOKEN = "made-up-token-no-server-ever-sees"
SECRET = "sk_live_9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOaXcVbN"


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


def bare(tmp_path: Path) -> Path:
    """A real bare repository whose HEAD points at main."""
    path = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", "-q", str(path)),
                   check=True, capture_output=True)
    subprocess.run(("git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/main"),
                   check=True, capture_output=True)
    return path


def seeded(tmp_path: Path) -> tuple[Path, Path]:
    """A working repository with one commit already on the remote."""
    remote = bare(tmp_path)
    work = tmp_path / "project"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "user.name", "Test")
    git(work, "remote", "add", "origin", str(remote))
    (work / ".gitignore").write_text(
        "node_modules\n.env\n.env.production\n", encoding="utf-8")
    (work / "README.md").write_text("start\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "initial")
    git(work, "branch", "-M", "main")
    git(work, "push", "-q", "origin", "main")
    return work, remote


def generate(work: Path) -> None:
    """What ProdPilot leaves in a project by the time this stage runs."""
    (work / "Dockerfile").write_text("FROM node:20-alpine\n", encoding="utf-8")
    (work / ".dockerignore").write_text("node_modules\n", encoding="utf-8")
    (work / ".env.example").write_text("PORT=\n", encoding="utf-8")
    (work / ".env.production").write_text(
        f"PORT=3000\nSTRIPE_KEY={SECRET}\n", encoding="utf-8")


# --------------------------------------------------------------------------
# what gets staged
# --------------------------------------------------------------------------


def test_the_staged_list_comes_from_the_modules_that_generate_the_files():
    """Derived, so a new generated file is covered without editing this."""
    from prodpilot.extraction import SHAPES
    from prodpilot.templates import TEMPLATES, Action

    expected = ({t.path for t in TEMPLATES.values()
                 if t.action is Action.CREATE_FILE and t.path}
                | {s.path for s in SHAPES.values() if getattr(s, "path", None)})

    assert set(GENERATED) == expected
    assert GENERATED == tuple(sorted(GENERATED))


def test_the_sealed_file_is_not_in_the_generated_list():
    """No template or shape produces it, which is the first of two protections."""
    assert ".env.production" not in GENERATED


def test_only_generated_files_that_exist_are_offered(tmp_path: Path):
    work, _ = seeded(tmp_path)
    generate(work)
    (work / "untracked-work.txt").write_text("in progress\n", encoding="utf-8")

    offered = pending(work)

    assert set(offered) <= set(GENERATED)
    assert "untracked-work.txt" not in offered
    assert "nginx.conf" not in offered, "a file the project does not have"


def test_an_ignored_generated_file_is_skipped(tmp_path: Path):
    """Checked rather than left to git to refuse."""
    work, _ = seeded(tmp_path)
    generate(work)
    (work / ".gitignore").write_text(
        "node_modules\n.env\n.env.production\nDockerfile\n", encoding="utf-8")

    assert "Dockerfile" not in pending(work)


# --------------------------------------------------------------------------
# a real push to a real local remote
# --------------------------------------------------------------------------


def test_a_real_push_reaches_the_remote(tmp_path: Path):
    work, remote = seeded(tmp_path)
    generate(work)

    result = run(work, token=TOKEN)

    assert result.ok is True, result.detail
    assert result.commit
    code, out = push.git(remote, "log", "-1", "--format=%s")
    assert out.strip() == MESSAGE


def test_the_commit_message_is_exactly_what_section_seven_says():
    assert MESSAGE == "chore: prodpilot production setup"


def test_the_sealed_file_is_never_staged(tmp_path: Path):
    """The load bearing test. A real secret must not reach a commit."""
    work, remote = seeded(tmp_path)
    generate(work)

    result = run(work, token=TOKEN)

    assert result.ok is True
    assert ".env.production" not in result.staged
    code, files = push.git(work, "show", "--name-only", "--format=", "HEAD")
    assert ".env.production" not in files
    code, tracked = push.git(work, "ls-files")
    assert ".env.production" not in tracked
    code, pushed = push.git(remote, "ls-tree", "-r", "--name-only", "main")
    assert ".env.production" not in pushed
    assert SECRET not in pushed


def test_work_the_developer_had_in_progress_is_not_swept_up(tmp_path: Path):
    work, _ = seeded(tmp_path)
    generate(work)
    (work / "untracked-work.txt").write_text("mine\n", encoding="utf-8")
    (work / "notes.md").write_text("also mine\n", encoding="utf-8")

    run(work, token=TOKEN)

    code, files = push.git(work, "show", "--name-only", "--format=", "HEAD")
    assert "untracked-work.txt" not in files
    assert "notes.md" not in files


def test_only_the_generated_files_land_in_the_commit(tmp_path: Path):
    work, _ = seeded(tmp_path)
    generate(work)

    result = run(work, token=TOKEN)

    code, files = push.git(work, "show", "--name-only", "--format=", "HEAD")
    for name in files.split():
        assert name in GENERATED, f"{name} is not a ProdPilot generated file"


def test_a_second_push_with_nothing_new_is_not_an_error(tmp_path: Path):
    work, _ = seeded(tmp_path)
    generate(work)
    assert run(work, token=TOKEN).ok is True

    again = run(work, token=TOKEN)

    assert again.ok is True


# --------------------------------------------------------------------------
# the token
# --------------------------------------------------------------------------


def test_the_token_is_read_the_way_module_1_3_reads_it(tmp_path: Path, monkeypatch):
    from prodpilot import config

    monkeypatch.setenv(config.CONFIG_HOME_ENV_VAR, str(tmp_path / "home"))
    config.save_credentials(config.Credentials(github_token="stored-token",
                                               render_api_key="k"))
    work, _ = seeded(tmp_path)
    generate(work)

    assert run(work).ok is True


def test_no_stored_token_is_reported(tmp_path: Path, monkeypatch):
    from prodpilot import config

    monkeypatch.setenv(config.CONFIG_HOME_ENV_VAR, str(tmp_path / "home"))
    work, _ = seeded(tmp_path)

    result = run(work)

    assert result.ok is False
    assert result.fail is Fail.NO_TOKEN
    assert "prodpilot setup" in result.detail


def test_the_token_never_reaches_the_repository_configuration(tmp_path: Path):
    """It is an argument to one command, never written into the remote."""
    work, _ = seeded(tmp_path)
    generate(work)

    run(work, token=TOKEN)

    code, remote = push.git(work, "remote", "get-url", "origin")
    assert TOKEN not in remote
    config_text = (work / ".git" / "config").read_text(encoding="utf-8")
    assert TOKEN not in config_text


def test_the_token_is_scrubbed_from_anything_returned():
    assert TOKEN not in scrub(f"failed for https://{USER}:{TOKEN}@github.com/o/r", TOKEN)
    assert "***" in scrub(f"a {TOKEN} b", TOKEN)
    assert scrub("", TOKEN) == ""


def test_the_push_url_carries_the_token_in_the_documented_form():
    made = authed("https://github.com/o/r.git", "T0K")

    assert made == f"https://{USER}:T0K@github.com/o/r.git"


def test_an_existing_user_in_the_remote_is_replaced():
    made = authed("https://someone@github.com/o/r.git", "T0K")

    assert made == f"https://{USER}:T0K@github.com/o/r.git"


# --------------------------------------------------------------------------
# remotes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("https://github.com/o/r.git", "https://github.com/o/r.git"),
        ("git@github.com:o/r.git", "https://github.com/o/r.git"),
        ("ssh://git@github.com/o/r.git", "https://github.com/o/r.git"),
    ],
)
def test_every_remote_form_becomes_https(remote: str, expected: str):
    assert https_url(remote) == expected


def test_a_local_remote_needs_no_token(tmp_path: Path):
    """Module 6.1 confirms the origin is GitHub before this stage runs."""
    remote = bare(tmp_path)

    url, carries = target(str(remote), TOKEN)

    assert local(str(remote)) is True
    assert url == str(remote)
    assert carries is False


def test_a_github_remote_carries_the_token(tmp_path: Path):
    url, carries = target("https://github.com/o/r.git", TOKEN)

    assert carries is True
    assert TOKEN in url


def test_a_repository_with_no_origin_is_reported(tmp_path: Path):
    work = tmp_path / "project"
    work.mkdir()
    git(work, "init", "-q")

    result = run(work, token=TOKEN)

    assert result.ok is False
    assert result.fail is Fail.NO_REMOTE


def test_a_remote_that_is_neither_is_reported(tmp_path: Path):
    work = tmp_path / "project"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "remote", "add", "origin", "not-a-url-or-a-path")

    result = run(work, token=TOKEN)

    assert result.ok is False
    assert result.fail is Fail.NO_REMOTE


def test_a_missing_origin_reads_as_none(tmp_path: Path):
    work = tmp_path / "project"
    work.mkdir()
    git(work, "init", "-q")

    assert origin_url(work) is None


# --------------------------------------------------------------------------
# failures, told apart
# --------------------------------------------------------------------------


def test_a_real_rejected_push_is_classified(tmp_path: Path):
    """Induced for real: someone else pushes first, so this one is behind."""
    remote = bare(tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q")
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "T")
    git(seed, "remote", "add", "origin", str(remote))
    (seed / "a.txt").write_text("1\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "seed")
    git(seed, "branch", "-M", "main")
    git(seed, "push", "-q", "origin", "main")

    def clone(name: str) -> Path:
        path = tmp_path / name
        subprocess.run(("git", "clone", "-q", str(remote), str(path)),
                       check=True, capture_output=True)
        git(path, "config", "user.email", "t@example.com")
        git(path, "config", "user.name", "T")
        return path

    theirs = clone("theirs")
    ours = clone("ours")
    (theirs / "b.txt").write_text("2\n", encoding="utf-8")
    git(theirs, "add", "-A")
    git(theirs, "commit", "-qm", "theirs")
    git(theirs, "push", "-q", "origin", "main")

    (ours / "Dockerfile").write_text("FROM node:20-alpine\n", encoding="utf-8")
    result = run(ours, token=TOKEN)

    assert result.ok is False
    assert result.fail is Fail.REJECTED
    assert "rejected" in result.detail.lower()


@pytest.mark.parametrize(
    "output",
    [
        "remote: Support for password authentication was removed.\n"
        "fatal: Authentication failed for 'https://github.com/o/r.git/'",
        "fatal: could not read Username for 'https://github.com': "
        "terminal prompts disabled",
        "remote: Invalid username or password.",
    ],
)
def test_an_authentication_failure_is_told_apart(output: str):
    """GitHub's own wording. Not induced, since that needs a real server."""
    fail, line = classify(output)

    assert fail is Fail.AUTH
    assert line


@pytest.mark.parametrize(
    "output",
    [
        "remote: Permission to octo/api.git denied to someone.\n"
        "fatal: unable to access 'https://github.com/octo/api.git/': "
        "The requested URL returned error: 403",
        "remote: Write access to repository not granted.",
    ],
)
def test_a_permission_failure_is_told_apart(output: str):
    fail, line = classify(output)

    assert fail is Fail.PERMISSION


def test_the_three_failures_are_distinguishable():
    """Section 7 asks for auth failures handled explicitly, not one error."""
    auth = classify("fatal: Authentication failed for 'https://github.com/o/r'")[0]
    permission = classify("remote: Permission to o/r.git denied to someone")[0]
    rejected = classify("! [rejected] main -> main (non-fast-forward)")[0]

    assert len({auth, permission, rejected}) == 3


def test_anything_else_is_unclassified_rather_than_mislabelled():
    fail, line = classify("fatal: the remote end hung up unexpectedly")

    assert fail is Fail.OTHER
    assert line


def test_git_being_unavailable_is_reported(tmp_path: Path, monkeypatch):
    work, _ = seeded(tmp_path)

    def absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(push.subprocess, "run", absent)
    code, output = push.git(work, "status")

    assert code == 1
    assert "not installed" in output


# --------------------------------------------------------------------------
# the result
# --------------------------------------------------------------------------


def test_the_result_serialises_whole(tmp_path: Path):
    work, _ = seeded(tmp_path)
    generate(work)

    payload = run(work, token=TOKEN).to_dict()

    assert set(payload) == {"project", "ok", "staged", "commit", "fail", "detail"}
    json.dumps(payload)


def test_no_token_appears_in_the_result(tmp_path: Path):
    work, _ = seeded(tmp_path)
    generate(work)

    payload = json.dumps(run(work, token=TOKEN).to_dict())

    assert TOKEN not in payload


def test_a_failed_push_reads_clearly():
    made = Push("p", False, fail=Fail.AUTH, detail="d")

    assert "not pushed" in made.summary()
    assert Fail.AUTH.value in made.summary()
