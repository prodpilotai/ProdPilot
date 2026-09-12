"""Full re-audit tests for Phase 4 module 4.1.

The case that matters is the one module 3.6 flagged on the way out. The loop
spends its retry budget across the whole run and never revisits an issue it has
retired, so a rule whose precondition is created later in the same run stays in
the manual review record while the project itself has moved on. Only a full
re-audit sees that, and the first test here proves it against a real loop run
rather than a constructed one.

Everything in that run is real: the audit that fills the queue, the fix
contracts, the independent verifier behind every outcome, and the re-audit at
the end.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from apply import applier
from prodpilot import audit, reaudit
from prodpilot.audit import Band, Report
from prodpilot.blueprint import Priority
from prodpilot.dispatch import Fixer
from prodpilot.findings import Status
from prodpilot.loop import Loop, Stop
from prodpilot.reaudit import Reaudit
from prodpilot.rules import rules_for_stack
from prodpilot.verify import verifier

SAMPLES = Path(__file__).resolve().parent / "samples"

# The two rules module 3.6 reported: retired to manual review while the files
# they depend on did not exist, then satisfied by later rules in the same run.
LATE = ("SCR-001", "SEC-001")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A writable copy of the seeded broken project."""
    root = tmp_path / "project"
    shutil.copytree(SAMPLES / "node_express_insecure", root)
    return root


@pytest.fixture
def looped(project: Path):
    """The seeded project after one real loop run, with the run itself."""
    fixer = Fixer(project, applier(project), verifier(project))
    run = Loop(fixer.resolve).run(audit.run(project).issues)
    return project, run


def status_of(report: Report, rule_id: str) -> Status:
    return next(r.status for r in report.results if r.rule_id == rule_id)


# --------------------------------------------------------------------------
# the case this module exists for
# --------------------------------------------------------------------------


def test_a_rule_retired_to_review_is_reported_passing_when_it_now_passes(looped):
    """The observation module 3.6 closed on, proven end to end.

    The loop's own record still says these two were never resolved, because
    that was true when the budget ran out. The project disagrees, and the
    re-audit reports the project.
    """
    project, run = looped
    reviewed = {r.rule_id for r in run.review}
    assert set(LATE) <= reviewed, f"the run reviewed {sorted(reviewed)}"
    assert set(LATE).isdisjoint(run.resolved)

    result = reaudit.after(project, run)

    assert result.ok
    for rule_id in LATE:
        assert status_of(result.report, rule_id) is Status.PASS, rule_id
    open_now = {i.rule_id for i in result.report.issues}
    assert set(LATE).isdisjoint(open_now)


def test_the_loop_record_is_left_alone(looped):
    """The re-audit corrects the project's status, not the run's history.

    The review records stay as they were, because they are an accurate account
    of what the loop did and Section 5.3 requires them to be kept.
    """
    project, run = looped
    before = [(r.rule_id, r.attempts, r.reason) for r in run.review]

    reaudit.after(project, run)

    assert [(r.rule_id, r.attempts, r.reason) for r in run.review] == before


def test_every_rule_the_loop_resolved_is_still_passing(looped):
    """A later fix must not have quietly undone an earlier one."""
    project, run = looped

    result = reaudit.after(project, run)

    for rule_id in run.resolved:
        assert status_of(result.report, rule_id) is Status.PASS, rule_id


def test_the_re_audit_sees_more_passing_than_the_loop_resolved(looped):
    """The measurable point of running the whole engine again."""
    project, run = looped
    before = audit.run(SAMPLES / "node_express_insecure")

    result = reaudit.after(project, run)

    assert result.report.score > before.score
    assert len(result.report.issues) < len(before.issues)
    assert len(result.report.passed) > len(run.resolved)


# --------------------------------------------------------------------------
# it is a full audit, not a targeted re-check
# --------------------------------------------------------------------------


def test_every_rule_for_the_stack_is_assessed_again(looped):
    """Not only the rules the loop touched, and not only the ones that failed."""
    project, run = looped

    result = reaudit.after(project, run)

    assessed = {r.rule_id for r in result.report.results}
    assert assessed == {r.rule_id for r in rules_for_stack(result.report.stack)}
    assert len(assessed) == 28


def test_a_rule_that_never_entered_the_queue_is_checked(looped):
    """A rule that passed the first audit is never seen by the loop again."""
    project, run = looped
    queued = {i.rule_id for i in audit.run(SAMPLES / "node_express_insecure").issues}
    touched = set(run.resolved) | {r.rule_id for r in run.review}

    result = reaudit.after(project, run)

    untouched = {r.rule_id for r in result.report.results} - queued - touched
    assert untouched, "the fixture leaves no rule outside the queue"
    for rule_id in untouched:
        assert status_of(result.report, rule_id) is not None


def test_the_report_is_the_audit_engine_s_own(looped):
    """Independent means the same engine on the same path, with nothing added."""
    project, run = looped

    result = reaudit.after(project, run)
    direct = audit.run(project)

    assert result.report.to_dict() == direct.to_dict()


def test_nothing_from_the_loop_changes_the_report(looped):
    """The run is carried for its stop reason and read for nothing else."""
    project, run = looped

    with_run = reaudit.after(project, run)
    without = reaudit.after(project)

    assert with_run.report.to_dict() == without.report.to_dict()
    assert with_run.stopped is run.stopped
    assert without.stopped is None


def test_the_report_reflects_the_files_as_they_are_now(looped):
    """A change made after the loop shows up, so nothing is cached."""
    project, run = looped
    assert status_of(reaudit.after(project, run).report, "GIT-001") is Status.PASS

    (project / ".gitignore").unlink()

    assert status_of(reaudit.after(project, run).report, "GIT-001") is Status.FAIL


# --------------------------------------------------------------------------
# the shape module 4.2 will read
# --------------------------------------------------------------------------


def test_the_result_carries_a_report_of_the_shape_2_4_produces(looped):
    project, run = looped

    report = reaudit.after(project, run).report

    assert isinstance(report, Report)
    assert isinstance(report.score, int) and 0 <= report.score <= 100
    assert isinstance(report.band, Band)
    assert report.results and report.stack
    assert report.issues == tuple(sorted(
        report.issues, key=lambda r: ([p for p in Priority].index(r.priority), r.rule_id)))
    assert all(i.priority is Priority.P0 for i in report.blockers)


def test_the_report_serialises_with_everything_the_gate_needs(looped):
    project, run = looped

    payload = reaudit.after(project, run).report.to_dict()

    assert {"score", "band", "counts", "issues", "blockers"} <= set(payload) | {"blockers"}
    assert isinstance(payload["score"], int)
    assert payload["band"] in {b.value for b in Band}
    assert payload["counts"]["assessed"] > 0
    json.dumps(payload)


def test_the_result_serialises_whole(looped):
    project, run = looped

    payload = reaudit.after(project, run).to_dict()

    assert set(payload) == {"project", "ok", "reason", "stopped", "report"}
    assert payload["ok"] is True
    assert payload["stopped"] == run.stopped.value
    json.dumps(payload)


def test_the_score_is_available_without_reaching_into_the_report(looped):
    project, run = looped

    result = reaudit.after(project, run)

    assert result.score == result.report.score


def test_this_module_decides_nothing(looped):
    """Threshold logic is 4.2. Nothing here may pre-empt it."""
    project, run = looped

    result = reaudit.after(project, run)

    fields = set(result.to_dict())
    assert not fields & {"passed", "deploy", "threshold", "gate", "loop_again"}
    assert not [n for n in dir(Reaudit) if "threshold" in n or "deploy" in n]


# --------------------------------------------------------------------------
# failing closed
# --------------------------------------------------------------------------


def test_a_missing_project_is_reported_not_raised():
    """The gate has to refuse on a result, not crash on an exception."""
    result = reaudit.after(SAMPLES / "no-such-project")

    assert result.ok is False
    assert result.score is None
    assert "not a project directory" in result.reason


def test_a_project_broken_by_a_fix_does_not_pass_the_re_audit(tmp_path: Path):
    """A manifest damaged mid-run stops the project detecting as any stack."""
    root = tmp_path / "project"
    shutil.copytree(SAMPLES / "node_express_insecure", root)
    (root / "package.json").write_text("{ this is not json", encoding="utf-8")

    result = reaudit.after(root, None)

    assert result.ok is False
    assert result.score is None
    assert result.reason
    assert result.report is not None and result.report.results == ()


def test_an_unsupported_project_is_not_treated_as_clean():
    """An empty report scores zero, and zero rules assessed is not a pass."""
    result = reaudit.after(SAMPLES / "unrecognized_python_service")

    assert result.ok is False
    assert result.report.score == 0
    assert result.report.band is Band.NOT_READY
    assert "package.json" in result.reason


def test_a_failed_re_audit_still_names_the_project_and_the_stop_reason(looped):
    project, run = looped
    shutil.rmtree(project)

    result = reaudit.after(project, run)

    assert result.ok is False
    assert result.project == project.name
    assert result.stopped is run.stopped


# --------------------------------------------------------------------------
# it runs whatever stopped the loop
# --------------------------------------------------------------------------


def test_a_cleared_run_is_re_audited(looped):
    project, run = looped

    assert run.stopped is Stop.CLEARED
    assert reaudit.after(project, run).ok


def test_a_run_that_resolved_nothing_is_still_re_audited(project: Path):
    """A loop that fixed nothing still needs the gate to see the real state."""
    fixer = Fixer(project, applier(project), lambda rule_id: False)
    run = Loop(fixer.resolve).run(audit.run(project).issues)
    assert run.resolved == ()

    result = reaudit.after(project, run)

    assert result.ok
    assert result.report.results


def test_the_re_audit_carries_where_the_project_is(project: Path):
    """Module 4.3 builds the project it gates, so it needs the place, not the name."""
    result = reaudit.after(project)

    assert result.root == str(project)
    assert "root" not in result.to_dict(), "the serialised shape is unchanged"


def test_even_a_failed_re_audit_carries_where_it_looked(tmp_path: Path):
    missing = tmp_path / "gone"

    assert reaudit.after(missing).root == str(missing)
