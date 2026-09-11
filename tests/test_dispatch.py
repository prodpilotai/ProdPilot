"""Fix dispatch tests for Phase 3 module 3.5.

Two things carry the module. Routing has to be right for all 50 rules, and the
agent's own report has to be incapable of producing an outcome.

Routing is proven by observation rather than by inspection. Each of the three
resolvers is wrapped so a test can see which one an issue actually reached, and
every rule in the frozen store is put through resolve, so the evidence is what
the code did, not what a table says it should do.

The verification seam is proven by disagreement. The agent claims one thing and
the verifier says the other, in both directions, and the outcome follows the
verifier every time.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from prodpilot import constraints, dispatch, extraction, templates
from prodpilot.audit import RuleResult, run
from prodpilot.dispatch import (
    FORMS,
    Claim,
    DispatchError,
    Fix,
    Fixer,
    Form,
    as_fix,
    broken,
    failing,
    form_of,
    instruct,
)
from prodpilot.findings import Finding, Status
from prodpilot.loop import Loop, Outcome, Step
from prodpilot.rules import ALL_RULES, FixType, get_rule

SAMPLES = Path(__file__).resolve().parent / "samples"
INSECURE = SAMPLES / "node_express_insecure"

OWNERS = {
    FixType.STATIC: templates.covers,
    FixType.DYNAMIC_PARAMETRIC: extraction.covers,
    FixType.DYNAMIC_DELEGATED: constraints.covers,
}


def issue(rule_id: str, file: str = "src/server.js", line: int = 4) -> RuleResult:
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    hit = Finding(rule_id, Status.FAIL, file, line, f"{rule_id} is not satisfied")
    return RuleResult(rule=rule, status=Status.FAIL, evidence=(hit,))


def agent(applied: bool = True, seen: list | None = None):
    """An agent double that reports what it is told to report."""

    def send(fix: Fix) -> Claim:
        if seen is not None:
            seen.append(fix)
        return Claim(fix.rule_id, applied, "test double")

    return send


def watch(fixer: Fixer) -> list[FixType]:
    """Record which resolver each issue reaches, without changing behaviour."""
    reached: list[FixType] = []

    def wrap(fix_type, resolve):
        def wrapped(one, attempt):
            reached.append(fix_type)
            return resolve(one, attempt)

        return wrapped

    for fix_type, resolve in list(fixer.resolvers.items()):
        fixer.resolvers[fix_type] = wrap(fix_type, resolve)
    return reached


# --------------------------------------------------------------------------
# routing, for all 50 rules
# --------------------------------------------------------------------------


def test_every_rule_reaches_the_resolver_for_its_fix_type():
    """The exit criterion, measured over the whole store rather than a sample."""
    fixer = Fixer(INSECURE, agent(), lambda rule_id: False)
    reached = watch(fixer)

    for rule in ALL_RULES:
        fixer.resolve(issue(rule.rule_id), 1)

    assert len(reached) == 50
    assert reached == [r.fix_type for r in ALL_RULES]


def test_the_resolver_table_is_keyed_by_fix_type_and_complete():
    fixer = Fixer(INSECURE, agent(), lambda rule_id: False)

    assert set(fixer.resolvers) == set(FixType)


def test_every_rule_is_owned_by_exactly_one_fix_module():
    """Routing would be ambiguous if two modules claimed the same rule."""
    for rule in ALL_RULES:
        owners = [t for t, covers in OWNERS.items() if covers(rule.rule_id)]

        assert owners == [rule.fix_type], f"{rule.rule_id} owned by {owners}"


def test_the_fifty_rules_split_as_the_store_records():
    counts = {t: len([r for r in ALL_RULES if r.fix_type is t]) for t in FixType}

    assert counts == {
        FixType.STATIC: 28,
        FixType.DYNAMIC_PARAMETRIC: 16,
        FixType.DYNAMIC_DELEGATED: 6,
    }
    assert sum(counts.values()) == 50


# --------------------------------------------------------------------------
# the two contract forms
# --------------------------------------------------------------------------


def test_the_form_map_matches_section_five_two():
    assert FORMS == {
        FixType.STATIC: Form.CONTENT,
        FixType.DYNAMIC_PARAMETRIC: Form.CONTENT,
        FixType.DYNAMIC_DELEGATED: Form.CONSTRAINT,
    }


def test_every_rule_has_a_form():
    for rule in ALL_RULES:
        assert isinstance(form_of(rule.fix_type), Form), rule.rule_id


def test_a_static_rule_renders_the_content_form():
    fix = instruct(INSECURE, issue("SEC-002"))

    assert fix.fix_type is FixType.STATIC
    assert fix.form is Form.CONTENT
    assert set(fix.body) == set(templates.render(issue("SEC-002")).to_dict())


def test_a_parametric_rule_renders_the_content_form_not_the_static_type():
    """Both first form producers return an Instruction, so the class cannot
    tell them apart and the store has to."""
    report = run(INSECURE)
    one = next(i for i in report.issues if i.rule.fix_type is FixType.DYNAMIC_PARAMETRIC)

    fix = instruct(INSECURE, one)

    assert fix.fix_type is FixType.DYNAMIC_PARAMETRIC
    assert fix.form is Form.CONTENT


def test_a_delegated_rule_renders_the_constraint_form():
    fix = instruct(INSECURE, issue("STR-001", file="src/controllers/userController.js"))

    assert fix.fix_type is FixType.DYNAMIC_DELEGATED
    assert fix.form is Form.CONSTRAINT
    assert fix.body["action"] == "author_within_constraint"
    assert "boundary" in fix.body and "forbidden" in fix.body


def test_the_two_forms_carry_different_fields():
    content = instruct(INSECURE, issue("SEC-002")).body
    constraint = instruct(INSECURE, issue("STR-001")).body

    assert "content" in content and "content" not in constraint
    assert "requirement" in constraint and "requirement" not in content


def test_a_fix_serialises_with_its_form_named():
    payload = instruct(INSECURE, issue("SEC-002")).to_dict()

    assert set(payload) == {"rule_id", "fix_type", "form", "contract"}
    assert payload["fix_type"] == "STATIC"
    assert payload["form"] == "content"


def test_wrapping_something_that_is_not_a_contract_is_refused():
    with pytest.raises(DispatchError):
        as_fix({"rule_id": "SEC-002"})


# --------------------------------------------------------------------------
# finding the issue to fix
# --------------------------------------------------------------------------


def test_failing_returns_the_issue_the_audit_raised():
    report = run(INSECURE)

    one = failing(report, "SEC-002")

    assert one.rule_id == "SEC-002"
    assert one.failed


def test_a_rule_the_project_already_passes_is_refused():
    """Producing a fix for a rule that passes would change working code."""
    report = run(SAMPLES / "node_express_secure")
    passing = report.passed[0].rule_id

    with pytest.raises(DispatchError):
        failing(report, passing)


def test_an_unknown_rule_id_is_refused():
    with pytest.raises(DispatchError):
        failing(run(INSECURE), "NOPE-000")


# --------------------------------------------------------------------------
# the verification seam
# --------------------------------------------------------------------------


def test_a_fixer_cannot_be_built_without_a_verifier():
    """No default, so an unwired verifier is impossible to miss."""
    with pytest.raises(DispatchError):
        Fixer(INSECURE, agent(), None)


def test_a_fixer_cannot_be_built_without_an_agent():
    with pytest.raises(DispatchError):
        Fixer(INSECURE, None, lambda rule_id: True)


def test_an_agent_claiming_success_does_not_resolve_when_the_rule_still_fails():
    """The whole point of Section 5.3, stated as a test."""
    fixer = Fixer(INSECURE, agent(applied=True), lambda rule_id: False)

    step = fixer.resolve(issue("SEC-002"), 1)

    assert fixer.claims[0].applied is True
    assert step.outcome is Outcome.UNRESOLVED


def test_an_agent_claiming_failure_resolves_when_the_rule_actually_passes():
    """The other direction, so the claim is ignored rather than inverted."""
    fixer = Fixer(INSECURE, agent(applied=False), lambda rule_id: True)

    step = fixer.resolve(issue("SEC-002"), 1)

    assert fixer.claims[0].applied is False
    assert step.outcome is Outcome.RESOLVED


def test_the_verifier_is_asked_even_when_the_agent_reports_it_did_nothing():
    asked = []
    fixer = Fixer(INSECURE, agent(applied=False), lambda rule_id: asked.append(rule_id) or False)

    fixer.resolve(issue("SEC-002"), 1)

    assert asked == ["SEC-002"]


def test_the_verifier_is_asked_about_the_rule_being_fixed():
    asked = []

    def verify(rule_id):
        asked.append(rule_id)
        return True

    fixer = Fixer(INSECURE, agent(), verify)
    fixer.resolve(issue("SEC-003"), 1)
    fixer.resolve(issue("STR-001"), 1)

    assert asked == ["SEC-003", "STR-001"]


def test_the_claim_and_the_verdict_are_both_recorded():
    fixer = Fixer(INSECURE, agent(applied=True), lambda rule_id: False)

    fixer.resolve(issue("SEC-002"), 1)

    assert len(fixer.claims) == len(fixer.verdicts) == 1
    assert fixer.claims[0].applied is True
    assert fixer.verdicts[0] is False


def test_the_outcome_follows_the_verifier_for_every_fix_type():
    for rule_id in ("SEC-002", "BLD-001", "STR-001"):
        yes = Fixer(INSECURE, agent(applied=False), lambda rule_id: True)
        no = Fixer(INSECURE, agent(applied=True), lambda rule_id: False)

        assert yes.resolve(issue(rule_id), 1).outcome is Outcome.RESOLVED, rule_id
        assert no.resolve(issue(rule_id), 1).outcome is Outcome.UNRESOLVED, rule_id


def test_a_verifier_that_raises_does_not_report_resolved():
    def boom(rule_id):
        raise RuntimeError("the checker crashed")

    step = Fixer(INSECURE, agent(), boom).resolve(issue("SEC-002"), 1)

    assert step.outcome is not Outcome.RESOLVED
    assert "the checker crashed" in step.detail


def test_an_agent_that_raises_does_not_report_resolved():
    def boom(fix):
        raise RuntimeError("the tool call failed")

    step = Fixer(INSECURE, boom, lambda rule_id: True).resolve(issue("SEC-002"), 1)

    assert step.outcome is not Outcome.RESOLVED
    assert "the tool call failed" in step.detail


# --------------------------------------------------------------------------
# the full path, issue to Step outcome
# --------------------------------------------------------------------------


def test_the_agent_receives_the_rendered_contract_for_each_fix_type():
    seen: list[Fix] = []
    fixer = Fixer(INSECURE, agent(seen=seen), lambda rule_id: True)

    fixer.resolve(issue("SEC-002"), 1)
    fixer.resolve(issue("STR-001"), 1)

    assert [f.form for f in seen] == [Form.CONTENT, Form.CONSTRAINT]
    assert seen[0].body["content"].strip()
    assert seen[1].body["requirement"].strip()


def test_a_real_audit_drives_the_whole_loop_to_a_verified_finish():
    """Issue in, resolver picked, contract produced, agent called, verifier asked."""
    report = run(INSECURE)
    seen: list[Fix] = []
    fixer = Fixer(INSECURE, agent(seen=seen), lambda rule_id: True)

    result = Loop(fixer.resolve).run(report.issues)

    assert len(seen) == len(fixer.claims) == len(fixer.verdicts)
    assert len(result.resolved) == len(seen)
    assert all(v is True for v in fixer.verdicts)


def test_a_run_where_nothing_verifies_ends_in_manual_review_not_success():
    report = run(INSECURE)
    fixer = Fixer(INSECURE, agent(applied=True), lambda rule_id: False)

    result = Loop(fixer.resolve).run(report.issues[:2])

    assert result.resolved == ()
    assert len(result.review) == 2
    assert len(fixer.claims) == 6


def test_the_loop_stops_at_the_retry_budget_under_a_failing_verifier():
    fixer = Fixer(INSECURE, agent(applied=True), lambda rule_id: False)

    result = Loop(fixer.resolve).run([issue("SEC-002")])

    assert len(result.attempts) == 3
    assert len(fixer.verdicts) == 3


def test_a_verifier_that_turns_true_resolves_on_a_later_attempt():
    """A fix that takes two tries, which is what the retry budget is for."""
    calls = []

    def verify(rule_id):
        calls.append(rule_id)
        return len(calls) >= 2

    fixer = Fixer(INSECURE, agent(), verify)
    result = Loop(fixer.resolve).run([issue("SEC-002")])

    assert len(calls) == 2
    assert [a.outcome for a in result.attempts] == [Outcome.UNRESOLVED, Outcome.RESOLVED]
    assert result.review == ()


def test_resolve_returns_the_step_shape_the_controller_expects():
    fixer = Fixer(INSECURE, agent(), lambda rule_id: True)

    assert isinstance(fixer.resolve(issue("SEC-002"), 2), Step)


def test_an_ambiguous_extraction_still_routes_to_the_parametric_resolver():
    """3.3 owns the refusal. This module must not turn it into a different type."""
    fixer = Fixer(SAMPLES / "amb_two_lockfiles", agent(), lambda rule_id: True)
    reached = watch(fixer)

    step = fixer.resolve(issue("BLD-001"), 1)

    assert reached == [FixType.DYNAMIC_PARAMETRIC]
    assert step.outcome is Outcome.BLOCKED
    assert fixer.claims == []


# --------------------------------------------------------------------------
# one fix must not break another
# --------------------------------------------------------------------------


def breaking(root: Path):
    """An agent double that ignores the contract and leaves the file unparseable."""

    def send(fix: Fix) -> Claim:
        (root / "src" / "server.js").write_text("app.use(}{\n", encoding="utf-8")
        return Claim(fix.rule_id, True, "test double")

    return send


def hardened(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(SAMPLES / "node_express_hardened", root)
    return root


def test_a_fix_that_breaks_a_passing_rule_is_reverted_and_blocked(tmp_path: Path):
    """The verifier says the rule passes, and the change is still refused."""
    root = hardened(tmp_path)
    before = (root / "src" / "server.js").read_bytes()
    fixer = Fixer(root, breaking(root), lambda rule_id: True)

    step = fixer.resolve(issue("SEC-002"), 1)

    assert step.outcome is Outcome.BLOCKED
    assert "broke" in step.detail and "reverted" in step.detail
    assert (root / "src" / "server.js").read_bytes() == before
    assert len(fixer.regressions) == 1
    assert fixer.regressions[0].reverted is True
    assert fixer.verdicts == [True], "the verifier's own verdict is still recorded"


def test_without_the_guard_the_verifier_alone_decides(tmp_path: Path):
    root = hardened(tmp_path)
    fixer = Fixer(root, breaking(root), lambda rule_id: True, guard=False)

    step = fixer.resolve(issue("SEC-002"), 1)

    assert step.outcome is Outcome.RESOLVED
    assert fixer.regressions == []


def test_an_unchanged_project_is_audited_once(monkeypatch):
    """The guard costs nothing extra when a change touched no file."""
    calls: list = []
    real = dispatch.standing
    monkeypatch.setattr(dispatch, "standing", lambda root: calls.append(root) or real(root))
    fixer = Fixer(INSECURE, agent(), lambda rule_id: False)

    fixer.resolve(issue("SEC-002"), 1)
    fixer.resolve(issue("SEC-003"), 1)

    assert len(calls) == 1


def test_only_a_pass_that_turns_into_a_failure_is_a_regression():
    before = {"A": Status.PASS, "B": Status.PASS, "C": Status.FAIL, "D": Status.PASS}
    after = {"A": Status.FAIL, "B": Status.SKIPPED, "C": Status.FAIL, "D": Status.UNPARSED}

    assert broken(before, after, "C") == ("A", "D")
    assert broken(before, after, "A") == ("D",), "the rule being fixed is not its own regression"
