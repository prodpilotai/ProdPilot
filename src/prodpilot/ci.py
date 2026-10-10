"""Readiness checks for continuous integration.

prodpilot audit runs the audit and then decides whether the project passes the
checks a CI job asked for. It never fixes, builds or deploys anything, and it
uses no network: the audit reads files and the local Git history only.

Exit codes
----------
0  the readiness check passed
1  the readiness check failed: a rule at or above --fail-on is failing, the
   score is under --min-score, the project regressed against --baseline, or
   the project's stack is not one ProdPilot supports
2  a tool or usage error: the path is not a project, the baseline ref cannot be
   resolved, Git is missing, or the report could not be produced

The baseline comparison
-----------------------
--baseline audits the same project at another Git ref, normally the base of a
pull request. The ref is checked out into a temporary worktree in the system
temp directory, never inside the project, and the worktree is removed whether
the audit succeeds or fails. The check fails when the score dropped, or when
any rule that passed on the base now fails, even if the score rose.

A shallow clone holds too little history to resolve the base. The error says so
and names the fix: fetch-depth: 0 on actions/checkout.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot import audit
from prodpilot.audit import Report
from prodpilot.blueprint import Priority
from prodpilot.findings import Status

logger = logging.getLogger(__name__)

PASSED, FAILED, ERROR = 0, 1, 2

# The priorities each --fail-on level blocks on.
FAIL_ON: dict[str, tuple[Priority, ...]] = {
    "p0": (Priority.P0,),
    "p1": (Priority.P0, Priority.P1),
    "none": (),
}


class CIError(Exception):
    """A tool or usage error: the check could not be carried out."""


@dataclass(frozen=True)
class Baseline:
    ref: str
    commit: str
    score: int
    newly_failing: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"ref": self.ref, "commit": self.commit, "score": self.score,
                "newly_failing": list(self.newly_failing)}


@dataclass(frozen=True)
class Outcome:
    report: Report
    reasons: tuple[str, ...]
    fail_on: str
    min_score: int | None
    baseline: Baseline | None = None
    notices: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.reasons

    @property
    def code(self) -> int:
        return PASSED if self.passed else FAILED

    def to_dict(self) -> dict[str, object]:
        report = self.report
        return {
            "project": report.project,
            "stack": report.stack.value,
            "score": report.score,
            "band": report.band.value,
            "passed": self.passed,
            "exit_code": self.code,
            "fail_on": self.fail_on,
            "min_score": self.min_score,
            "reasons": list(self.reasons),
            "failing": [{"rule_id": r.rule_id, "priority": r.priority.value,
                         "domain": r.domain.value, "fix_type": r.fix_type.value}
                        for r in report.issues],
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "notices": list(self.notices),
        }

    def summary(self) -> str:
        report = self.report
        lines = [f"{report.project}: {report.score}/100 {report.band.value}, "
                 f"{len(report.issues)} failing rule(s), {len(report.blockers)} critical"]
        for r in report.issues:
            lines.append(f"  {r.priority.value} {r.rule_id} {r.rule.description}")
        if self.baseline:
            lines.append(f"Baseline {self.baseline.ref} ({self.baseline.commit[:12]}): "
                         f"{self.baseline.score}/100")
            for rule_id in self.baseline.newly_failing:
                lines.append(f"  newly failing: {rule_id}")
        for notice in self.notices:
            lines.append(f"Notice: {notice}")
        if self.passed:
            lines.append("Readiness check passed.")
        else:
            lines.append("Readiness check failed:")
            lines += [f"  {reason}" for reason in self.reasons]
        return "\n".join(lines)


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=300, check=False)
    except FileNotFoundError as exc:
        raise CIError("git is not installed or is not on PATH") from exc


def resolve(repo: Path, ref: str) -> str:
    """The commit a ref names, or a CIError that says why it cannot be found."""
    found = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip()
    shallow = git(repo, "rev-parse", "--is-shallow-repository")
    if shallow.stdout.strip() == "true":
        raise CIError(
            f"the baseline ref {ref} cannot be resolved because this is a shallow clone. "
            f"In GitHub Actions, set fetch-depth: 0 on actions/checkout so the base "
            f"commit is available.")
    raise CIError(f"the baseline ref {ref} does not name a commit in this repository")


def audit_at(root: Path, ref: str) -> tuple[Report, str]:
    """Audit the project as it was at ref, in a temporary worktree removed afterwards."""
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        raise CIError(f"--baseline needs a Git repository, and {root} is not inside one")
    repo = Path(top.stdout.strip()).resolve()
    inner = root.resolve().relative_to(repo)
    commit = resolve(repo, ref)

    holder = Path(tempfile.mkdtemp(prefix="prodpilot-base-"))
    tree = holder / "tree"
    pending: BaseException | None = None
    try:
        added = git(repo, "worktree", "add", "--detach", str(tree), commit)
        if added.returncode != 0:
            raise CIError(f"could not check out {ref} into a temporary worktree: "
                          f"{added.stderr.strip()}")
        project = tree / inner
        if not project.is_dir():
            raise CIError(f"{inner.as_posix() or '.'} does not exist at {ref}, so there is "
                          f"no baseline to compare with")
        return audit.run(project), commit
    except BaseException as exc:
        pending = exc
        raise
    finally:
        cleanup(repo, tree, holder, pending)


def cleanup(repo: Path, tree: Path, holder: Path, pending: BaseException | None) -> None:
    """Remove the worktree and its temporary directory.

    A failure here is reported. When the audit itself already failed, that
    failure is the one raised and this one is logged beside it; otherwise this
    one is raised, because a worktree left behind is a fault.
    """
    problems: list[str] = []
    if tree.exists():
        removed = git(repo, "worktree", "remove", "--force", str(tree))
        if removed.returncode != 0:
            problems.append(f"git worktree remove failed: {removed.stderr.strip()}")
    pruned = git(repo, "worktree", "prune")
    if pruned.returncode != 0:
        problems.append(f"git worktree prune failed: {pruned.stderr.strip()}")
    try:
        shutil.rmtree(holder)
    except OSError as exc:
        problems.append(f"could not remove {holder}: {exc}")
    if not problems:
        return
    message = "the temporary baseline worktree was not fully removed: " + "; ".join(problems)
    if pending is not None:
        logger.error("%s", message)
        return
    raise CIError(message)


def compare(head: Report, base: Report, ref: str, commit: str) -> Baseline:
    before = {r.rule_id: r.status for r in base.results}
    newly = tuple(r.rule_id for r in head.issues if before.get(r.rule_id) is Status.PASS)
    return Baseline(ref=ref, commit=commit, score=base.score, newly_failing=newly)


def check(root: str | Path, fail_on: str = "p0", min_score: int | None = None,
          baseline: str | None = None) -> Outcome:
    """Audit a project and apply the requested readiness checks.

    Raises CIError for a tool or usage error and NotADirectoryError when root is
    not a project.
    """
    if fail_on not in FAIL_ON:
        raise CIError(f"--fail-on must be one of {', '.join(FAIL_ON)}, not {fail_on}")
    if min_score is not None and not 0 <= min_score <= 100:
        raise CIError(f"--min-score must be between 0 and 100, not {min_score}")
    base = Path(root)
    report = audit.run(base)
    reasons: list[str] = []

    if not report.results:
        reasons.append(f"the project could not be audited: {report.detection.reason}")
    blocking = [r for r in report.issues if r.priority in FAIL_ON[fail_on]]
    if blocking:
        reasons.append(f"{len(blocking)} rule(s) at {fail_on.upper()} or above are failing: "
                       f"{', '.join(r.rule_id for r in blocking)}")
    if min_score is not None and report.score < min_score:
        reasons.append(f"the score {report.score} is below the minimum of {min_score}")

    found: Baseline | None = None
    if baseline:
        before, commit = audit_at(base, baseline)
        found = compare(report, before, baseline, commit)
        if report.score < before.score:
            reasons.append(f"the score dropped from {before.score} at {baseline} "
                           f"to {report.score}")
        if found.newly_failing:
            reasons.append(f"rule(s) that passed at {baseline} now fail: "
                           f"{', '.join(found.newly_failing)}")
    return Outcome(report=report, reasons=tuple(reasons), fail_on=fail_on,
                   min_score=min_score, baseline=found, notices=report.notices)
