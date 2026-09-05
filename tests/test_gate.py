"""Scoring gate tests for Phase 4 module 4.2.

The module's real job is the multi cycle path, so most of what is here drives
more than one cycle. A single cycle would exercise the threshold comparison and
nothing else.

Where a test uses the real fixer, everything in the run is real: the audits, the
fix contracts, the independent verifier behind every outcome, the score and the
review records. Where a test uses a scripted resolve step, that is said so in
the test, and only the fix agent is scripted. The audits, the scoring, the
threshold and the review records stay real in every test here, because those are
what this module is made of.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from apply import applier
from prodpilot import audit, gate, reaudit
from prodpilot.audit import Band, Report, RuleResult, band_of, score_of
from prodpilot.blueprint import Priority
from prodpilot.dispatch import Fixer
from prodpilot.findings import Status
from prodpilot.gate import THRESHOLD, Decision, clears, run
from prodpilot.loop import CEILING, Outcome, Review, Step
from prodpilot.reaudit import Reaudit
from prodpilot.verify import verifier

SAMPLES = Path(__file__).resolve().parent / "samples"


def copy(name: str, into: Path) -> Path:
    root = into / "project"
    shutil.copytree(SAMPLES / name, root)
    return root


def fixer_for(root: Path) -> Fixer:
    """The real thing: real contracts, real edits, real verification."""
    return Fixer(root, applier(root), verifier(root))


def drop_line(path: Path, needle: str) -> None:
    kept = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip() != needle]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def drop_key(path: Path, *keys: str) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    holder = document
    for key in keys[:-1]:
        holder = holder[key]
    del holder[keys[-1]]
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# the threshold itself
# --------------------------------------------------------------------------


def test_the_threshold_is_the_production_ready_boundary():
    """No document states a number, so the choice is pinned here.

    Section 6 gives the bands and 90 opens Production Ready. Anything lower
    gates on a band that does not claim the project is deployable.
    """
    assert THRESHOLD == 90
    assert band_of(THRESHOLD) is Band.PRODUCTION_READY
    assert band_of(THRESHOLD - 1) is Band.NEARLY_READY


def test_the_score_comes_from_one_place_so_4_3_can_move_it():
    """Named apart from audit.score_of, which computes a score rather than
    reading one, so the two cannot be confused at a call site."""
    result = reaudit.after(SAMPLES / "react_vite_ready")

    assert gate.reading(result) == result.score == result.report.score


def test_the_whole_decision_follows_the_one_score_source(monkeypatch, tmp_path: Path):
    """Module 4.3's claim, tested by moving the seam rather than trusting it.

    The project on disk does not change. Making the gate's single score source
    report a different number has to move the score, the band and the readiness
    together. Anything that still reported the old number would be a second
    source, and 4.3 would then be more than a one line change.
    """
    root = copy("react_vite_ready", tmp_path)
    (root / ".dockerignore").unlink()
    real = audit.run(root)
    assert real.score >= THRESHOLD and real.blockers == ()

    monkeypatch.setattr(gate, "reading", lambda result: 50 if result.ok else None)
    decision = run(root, lambda issue, attempt: Step(Outcome.UNRESOLVED, "left alone"))

    assert decision.score == 50
    assert decision.band == Band.NEEDS_WORK.value
    assert decision.ready is False
    assert "50" in decision.reason


def test_the_band_is_not_taken_from_the_report(tmp_path: Path):
    """The band has to describe the score the gate acted on, not another one."""
    root = copy("react_vite_ready", tmp_path)

    decision = run(root, fixer_for(root).resolve)

    assert decision.band == band_of(decision.score).value


def test_the_blocker_rule_reads_rule_statuses_not_the_score():
    """So it is unaffected by which source the score comes from.

    Both reports below fail the same P0. The scores differ, and the refusal and
    its reason do not.
    """
    one = clears(rebuilt("react_vite_ready", {"SEC-005"}))
    two = clears(rebuilt("react_vite_ready", {"SEC-005", "BLD-011", "BLD-012"}))

    assert one[0] is False and two[0] is False
    assert "SEC-005" in one[1] and "SEC-005" in two[1]


def test_no_model_is_used_anywhere_in_this_module():
    """4.2 gates on the raw audit score. The calibrated score is 4.3's.

    Checked against the parsed module rather than its text, because the comment
    at reading() names the classifier it will one day read from, and naming a
    dependency in a comment is not depending on it.
    """
    import ast

    tree = ast.parse(Path(gate.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"sklearn", "joblib", "numpy", "pandas", "pickle"}

    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not called & {"predict", "predict_proba", "fit", "load_model"}


def test_the_ceiling_is_the_loop_s_own_not_a_second_one():
    """Module 3.1 owns the bound. This module must not restate it."""
    import inspect

    assert inspect.signature(run).parameters["ceiling"].default == CEILING
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "CEILING = " not in source
    assert "RETRIES" not in source


# --------------------------------------------------------------------------
# what opens the gate
# --------------------------------------------------------------------------


def rebuilt(name: str, failing: set[str]) -> Reaudit:
    """A real report with a chosen set of rules failing.

    Built from the real rules and the real scoring function, so the score is
    the one the engine would compute rather than a number written by hand.
    """
    real = audit.run(SAMPLES / name)
    results = [
        RuleResult(rule=r.rule,
                   status=Status.FAIL if r.rule_id in failing else Status.PASS,
                   evidence=r.evidence)
        for r in real.results
    ]
    score = score_of(results)
    report = Report(project=name, stack=real.stack, detection=real.detection,
                    score=score, band=band_of(score), results=tuple(results))
    return Reaudit(project=name, report=report)


def test_a_clean_project_clears_the_gate():
    ok, why = clears(rebuilt("react_vite_ready", set()))

    assert ok is True
    assert "meets the threshold" in why


def test_a_low_score_does_not_clear_the_gate():
    """Three P1 rules, chosen so the score falls without a P0 being involved.

    That keeps this test on the score alone rather than on the blocker rule.
    """
    result = rebuilt("react_vite_ready", {"BLD-011", "BLD-012", "BLD-013"})
    assert result.report.blockers == ()
    assert result.report.score < THRESHOLD

    ok, why = clears(result)

    assert ok is False
    assert "below the threshold" in why


def test_a_critical_failure_blocks_deployment_even_above_the_threshold():
    """Module 2.4 left this policy here in as many words.

    One failed P0 with everything else passing still scores above 90, so a gate
    on the score alone would deploy a project with a critical rule open.
    """
    result = rebuilt("react_vite_ready", {"SEC-005"})
    assert result.report.score >= THRESHOLD
    assert audit.get_rule("SEC-005").priority is Priority.P0

    ok, why = clears(result)

    assert ok is False
    assert "critical" in why and "SEC-005" in why


def test_the_same_project_clears_once_the_critical_rule_passes():
    """The other direction, so the block is the P0 and not something else."""
    ok, _ = clears(rebuilt("react_vite_ready", set()))

    assert ok is True


def test_a_project_that_could_not_be_audited_does_not_clear():
    """Fails closed. An absent score is not a passing score."""
    ok, why = clears(reaudit.after(SAMPLES / "no-such-project"))

    assert ok is False
    assert why


def test_the_threshold_is_a_parameter_not_a_constant_in_the_logic():
    result = rebuilt("react_vite_ready", {"BLD-011"})

    assert clears(result, threshold=100)[0] is False
    assert clears(result, threshold=50)[0] is True


# --------------------------------------------------------------------------
# one cycle, fully real
# --------------------------------------------------------------------------


def test_a_project_already_at_threshold_is_ready_without_changes(tmp_path: Path):
    root = copy("react_vite_ready", tmp_path)
    before = audit.run(root)
    assert before.score >= THRESHOLD

    decision = run(root, fixer_for(root).resolve)

    assert decision.ready is True
    assert decision.score >= THRESHOLD
    assert decision.band == Band.PRODUCTION_READY.value
    assert decision.blockers == ()


def test_a_fixable_project_clears_the_gate_after_one_cycle(tmp_path: Path):
    """Section 5's first exit criterion, with everything real."""
    root = copy("react_vite_ready", tmp_path)
    (root / "nginx.conf").unlink()
    before = audit.run(root)
    assert before.score < THRESHOLD

    decision = run(root, fixer_for(root).resolve)

    assert decision.ready is True
    assert decision.cycles == 1
    assert "BLD-009" in decision.resolved
    assert decision.score > before.score


# --------------------------------------------------------------------------
# more than one cycle, fully real
# --------------------------------------------------------------------------


@pytest.fixture
def looped(tmp_path: Path):
    """The seeded broken project taken through the whole gate, for real."""
    root = copy("node_express_insecure", tmp_path)
    before = audit.run(root)
    decision = run(root, fixer_for(root).resolve)
    return root, before, decision


def test_a_real_project_runs_more_than_one_cycle(looped):
    """The module's core job, on a real fixture rather than a scripted one."""
    _, _, decision = looped

    assert decision.cycles > 1


def test_the_second_cycle_works_on_a_rule_the_first_never_queued(looped):
    """Why looping again is worth doing at all.

    A rule can be unassessable at the start and become assessable once a fix
    lands. Reading the port from the environment is what makes the environment
    template rule checkable, and it then fails.
    """
    root, before, decision = looped
    queued = {i.rule_id for i in before.issues}
    reviewed = {r.rule_id for r in decision.review}

    assert "ENV-001" not in queued
    assert "ENV-001" in reviewed


def test_the_gate_refuses_when_the_score_stays_below_threshold(looped):
    _, _, decision = looped

    assert decision.ready is False
    assert decision.score < THRESHOLD
    assert decision.reason


def test_the_loop_still_improved_the_project(looped):
    _, before, decision = looped

    assert decision.score > before.score
    assert decision.resolved


def test_review_records_are_aggregated_across_every_cycle(looped):
    """Section 5 asks for what remains, not for what one cycle left behind."""
    _, _, decision = looped
    reviewed = [r.rule_id for r in decision.review]

    assert len(reviewed) == len(set(reviewed)), "a rule was reported twice"
    assert "SEC-002" in reviewed, "a first cycle failure is missing"
    assert "ENV-001" in reviewed, "a later cycle failure is missing"


def test_review_records_carry_exact_locations_and_reasons(looped):
    """Section 5's wording, checked field by field on a real run."""
    _, _, decision = looped

    assert decision.review
    for record in decision.review:
        assert isinstance(record, Review), "a second review shape was invented"
        assert record.rule_id and record.priority and record.domain
        assert record.fix_type and record.requirement.strip()
        assert record.reason.strip()
        assert record.attempts >= 1

    located = [r for r in decision.review if r.locations]
    assert located, "no review record carried a location"
    for record in located:
        for place in record.locations:
            assert place.get("file")


def test_the_decision_serialises_whole(looped):
    _, _, decision = looped

    payload = decision.to_dict()

    assert set(payload) == {
        "project", "ready", "reason", "threshold", "score", "band",
        "cycles", "stopped", "resolved", "blockers", "manual_review",
    }
    assert payload["ready"] is False
    assert payload["manual_review"]
    json.dumps(payload)


# --------------------------------------------------------------------------
# the ceiling
# --------------------------------------------------------------------------


def chain(root: Path):
    """A scripted resolve step where every fix causes one new violation.

    Only the fix agent is scripted. The audits, the scoring and the review
    records stay real. A fix that introduces a fresh violation is exactly the
    case the iteration ceiling exists for, so it is what the ceiling has to be
    tested with, and it cannot be produced by fixing a project correctly.
    """
    steps = {
        "BLD-002": lambda: shutil.rmtree(root / ".github"),
        "BLD-003": lambda: drop_key(root / "package.json", "scripts", "start"),
        "BLD-004": lambda: drop_key(root / "package.json", "engines"),
        "BLD-005": lambda: (root / "src" / "server.js").write_text(
            (root / "src" / "server.js").read_text(encoding="utf-8")
            .replace('"/health"', '"/version"'), encoding="utf-8"),
        "OBS-001": lambda: drop_line(root / "src" / "server.js", "app.use(helmet());"),
    }

    def resolve(issue: RuleResult, attempt: int) -> Step:
        step = steps.pop(issue.rule_id, None)
        if step is None:
            return Step(Outcome.UNRESOLVED, "this one is left alone")
        step()
        return Step(Outcome.RESOLVED, "fixed, and something else broke")

    return resolve


def test_the_ceiling_stops_a_run_that_keeps_finding_new_work(tmp_path: Path):
    """Section 5's second exit criterion, and the reason for the ceiling."""
    root = copy("node_express_ready", tmp_path)
    (root / ".dockerignore").unlink()

    decision = run(root, chain(root))

    assert decision.cycles == CEILING
    assert decision.stopped == "ceiling"
    assert decision.ready is False


def test_the_ceiling_run_still_reports_what_is_left(tmp_path: Path):
    root = copy("node_express_ready", tmp_path)
    (root / ".dockerignore").unlink()

    decision = run(root, chain(root))

    assert decision.review
    for record in decision.review:
        assert record.rule_id and record.reason.strip()
        assert 1 <= record.attempts <= 3


def test_a_lower_ceiling_is_respected(tmp_path: Path):
    root = copy("node_express_ready", tmp_path)
    (root / ".dockerignore").unlink()

    decision = run(root, chain(root), ceiling=2)

    assert decision.cycles == 2
    assert decision.stopped == "ceiling"


def test_the_gate_stops_as_soon_as_the_threshold_is_met(tmp_path: Path):
    """Meeting the threshold ends the run rather than spending the ceiling."""
    root = copy("react_vite_ready", tmp_path)
    (root / "nginx.conf").unlink()
    assert audit.run(root).score < THRESHOLD

    decision = run(root, fixer_for(root).resolve)

    assert decision.ready is True
    assert decision.cycles < CEILING
    assert decision.stopped == "cleared"


# --------------------------------------------------------------------------
# failing closed
# --------------------------------------------------------------------------


def test_a_missing_project_is_a_decision_not_an_exception():
    decision = run(SAMPLES / "no-such-project", lambda issue, attempt: None)

    assert isinstance(decision, Decision)
    assert decision.ready is False
    assert decision.score is None
    assert "not a project directory" in decision.reason


def test_a_project_of_no_supported_stack_does_not_deploy():
    decision = run(SAMPLES / "unrecognized_python_service", lambda issue, attempt: None)

    assert decision.ready is False
    assert decision.score is None


def test_a_project_broken_during_the_run_does_not_deploy(tmp_path: Path):
    """A manifest damaged mid run leaves nothing to gate on, so the gate refuses."""
    root = copy("react_vite_ready", tmp_path)

    def wreck(issue, attempt):
        (root / "package.json").write_text("{ not json", encoding="utf-8")
        return Step(Outcome.UNRESOLVED, "left the manifest unreadable")

    (root / ".dockerignore").unlink()
    decision = run(root, wreck)

    assert decision.ready is False
    assert decision.score is None
