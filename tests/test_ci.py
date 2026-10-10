"""prodpilot audit for CI: exit codes, thresholds and the baseline comparison."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prodpilot import ci
from prodpilot.cli import app

SAMPLES = Path(__file__).resolve().parent / "samples"


def run(*args: str) -> tuple[int, str]:
    done = CliRunner().invoke(app, ["audit", *args])
    return done.exit_code, done.stdout


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=ProdPilot Test", "-c",
                           "user.email=test@prodpilot.invalid", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def repo(tmp_path: Path, sample: str = "node_express_insecure") -> Path:
    root = tmp_path / "repo"
    shutil.copytree(SAMPLES / sample, root)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def worktrees(root: Path) -> list[str]:
    return [line for line in git(root, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")]


# ---------------------------------------------------------------- exit codes

def test_a_failing_p0_fails_by_default() -> None:
    code, out = run(str(SAMPLES / "node_express_insecure"))
    assert code == 1
    assert "Readiness check failed" in out


def test_fail_on_none_and_no_minimum_passes_a_weak_project() -> None:
    code, out = run(str(SAMPLES / "node_express_insecure"), "--fail-on", "none")
    assert code == 0 and "Readiness check passed" in out


def test_a_ready_project_passes() -> None:
    code, _ = run(str(SAMPLES / "node_express_gated"), "--min-score", "90")
    assert code == 0


def test_fail_on_p1_catches_a_p1_failure_that_p0_lets_through(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(SAMPLES / "node_express_gated", root)
    (root / ".dockerignore").unlink()
    found = ci.check(root, fail_on="none").report
    assert [(r.rule_id, r.priority.value) for r in found.issues] == [("BLD-002", "P1")]
    assert ci.check(root, fail_on="p0").passed is True
    p1 = ci.check(root, fail_on="p1")
    assert p1.passed is False and "BLD-002" in p1.reasons[0]


def test_the_minimum_score_is_enforced() -> None:
    code, out = run(str(SAMPLES / "react_vite_ready"), "--fail-on", "none",
                    "--min-score", "100", "--ci")
    summary = json.loads(out)
    assert code == 1 and summary["passed"] is False
    assert "below the minimum of 100" in summary["reasons"][0]


def test_ci_output_is_json_with_the_exit_code() -> None:
    code, out = run(str(SAMPLES / "node_express_insecure"), "--ci")
    summary = json.loads(out)
    assert summary["exit_code"] == code == 1
    assert summary["score"] == 13 and summary["failing"]


def test_an_unrecognised_project_fails_the_check() -> None:
    code, out = run(str(SAMPLES / "unrecognized_python_service"), "--ci")
    assert code == 1 and "could not be audited" in json.loads(out)["reasons"][0]


def test_a_missing_path_is_a_tool_error(tmp_path: Path) -> None:
    code, out = run(str(tmp_path / "missing"), "--ci")
    assert code == 2 and json.loads(out)["exit_code"] == 2


@pytest.mark.parametrize("flags", [["--fail-on", "p3"], ["--min-score", "101"]])
def test_a_bad_flag_value_is_a_usage_error(flags: list[str]) -> None:
    code, _ = run(str(SAMPLES / "react_vite_app"), *flags)
    assert code == 2


# ---------------------------------------------------------------- baseline

def test_a_rule_that_regresses_fails_even_when_the_score_rises(tmp_path: Path) -> None:
    """Fix a P0 and break a P5 in one change: the score rises, the check still fails."""
    root = repo(tmp_path)
    (root / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ignore node_modules")
    base = git(root, "rev-parse", "HEAD")
    (root / ".gitignore").write_text(".env\n.env.production\n", encoding="utf-8")

    outcome = ci.check(root, fail_on="none", baseline=base)

    assert outcome.report.score > outcome.baseline.score
    assert outcome.baseline.newly_failing == ("GIT-002",)
    assert outcome.passed is False
    assert any("now fail: GIT-002" in r for r in outcome.reasons)
    code, out = run(str(root), "--fail-on", "none", "--baseline", base)
    assert code == 1 and "newly failing: GIT-002" in out


def test_a_score_drop_fails(tmp_path: Path) -> None:
    root = repo(tmp_path, "node_express_gated")
    (root / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    outcome = ci.check(root, fail_on="none", baseline="HEAD")
    assert outcome.report.score < outcome.baseline.score
    assert any("score dropped" in r for r in outcome.reasons)


def test_an_unchanged_project_passes_against_itself(tmp_path: Path) -> None:
    root = repo(tmp_path, "node_express_gated")
    code, out = run(str(root), "--baseline", "HEAD", "--ci")
    summary = json.loads(out)
    assert code == 0 and summary["baseline"]["newly_failing"] == []


def test_the_worktree_is_removed_after_a_baseline_run(tmp_path: Path) -> None:
    root = repo(tmp_path)
    before = worktrees(root)
    ci.check(root, fail_on="none", baseline="HEAD")
    assert worktrees(root) == before
    assert not list(Path(__import__("tempfile").gettempdir()).glob("prodpilot-base-*/tree"))


def test_the_worktree_is_removed_when_the_audit_fails(tmp_path: Path, monkeypatch) -> None:
    root = repo(tmp_path)
    before = worktrees(root)
    seen: list[Path] = []

    def broken(path):
        seen.append(Path(path))
        raise OSError("the disk went away")

    monkeypatch.setattr(ci.audit, "run", broken)
    with pytest.raises(OSError, match="the disk went away"):
        ci.audit_at(root, "HEAD")
    holder = next(p for p in [seen[0], *seen[0].parents] if p.name.startswith("prodpilot-base-"))
    assert seen and not seen[0].exists() and not holder.exists()
    assert worktrees(root) == before


def test_a_shallow_clone_says_to_fetch_full_history(tmp_path: Path) -> None:
    origin = repo(tmp_path)
    (origin / "extra.txt").write_text("x\n", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "second")
    first = git(origin, "rev-parse", "HEAD~1")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(clone)],
                   check=True, capture_output=True)

    with pytest.raises(ci.CIError, match="fetch-depth: 0"):
        ci.check(clone, fail_on="none", baseline=first)
    code, out = run(str(clone), "--baseline", first, "--ci")
    assert code == 2 and "fetch-depth: 0" in json.loads(out)["error"]


def test_an_unknown_ref_is_a_tool_error(tmp_path: Path) -> None:
    root = repo(tmp_path)
    code, out = run(str(root), "--baseline", "no-such-branch", "--ci")
    assert code == 2 and "does not name a commit" in json.loads(out)["error"]


def test_a_baseline_outside_git_is_a_tool_error(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    shutil.copytree(SAMPLES / "react_vite_app", root)
    code, _ = run(str(root), "--baseline", "HEAD")
    assert code == 2
