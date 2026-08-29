"""Loop controller tests for Phase 3 module 3.1.

The controller owns sequencing and bounds, so that is what is tested: ordering,
the retry budget, the iteration ceiling, the manual review record, and above all
that it terminates. The resolve step is a double throughout, since no fix logic
exists yet and none belongs in this module.
"""

from __future__ import annotations

import pytest

from prodpilot.audit import RuleResult, run
from prodpilot.blueprint import Priority
from prodpilot.findings import Finding, Status
from prodpilot.loop import (
    CEILING,
    RETRIES,
    Attempt,
    Cycle,
    Loop,
    LoopError,
    Outcome,
    Step,
    Stop,
    order,
)
from prodpilot.rules import ALL_RULES, get_rule

SAMPLE = "tests/samples/node_express_api"


def issue(rule_id: str, file: str = "src/server.js", line: int = 1) -> RuleResult:
    """A failing RuleResult for a real rule from the frozen store."""
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    hit = Finding(rule_id, Status.FAIL, file, line, f"{rule_id} is not satisfied")
    return RuleResult(rule=rule, status=Status.FAIL, evidence=(hit,))


def always(outcome: Outcome, detail: str = ""):
    """A resolve step with a fixed answer."""
    return lambda i, n: Step(outcome, detail)


def resolves_on(number: int):
    """A resolve step that succeeds on a given attempt number."""
    return lambda i, n: Step(Outcome.RESOLVED if n >= number else Outcome.UNRESOLVED)


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def test_queue_puts_p0_first():
    queued = order([issue("OBS-004"), issue("SEC-002"), issue("API-001")])

    assert [r.priority for r in queued] == [Priority.P0, Priority.P2, Priority.P4]


def test_queue_orders_every_tier_correctly():
    ids = ["GIT-001", "STR-001", "OBS-001", "API-001", "BLD-001", "SEC-002"]
    queued = order([issue(i) for i in ids])

    ranks = [list(Priority).index(r.priority) for r in queued]
    assert ranks == sorted(ranks)
    assert queued[0].priority is Priority.P0
    assert queued[-1].priority is Priority.P5


def test_equal_priority_is_ordered_by_rule_id():
    """A stable order matters, since a run has to be reproducible."""
    queued = order([issue("SEC-004"), issue("ENV-002"), issue("SEC-002")])

    assert [r.rule_id for r in queued] == ["ENV-002", "SEC-002", "SEC-004"]


def test_the_audit_report_queue_is_already_in_this_order():
    """The controller and the report must not disagree about what is next."""
    issues = run(SAMPLE).issues

    assert list(issues) == list(order(issues))


def test_issues_are_worked_one_at_a_time_in_order():
    seen: list[str] = []

    def watch(i, n):
        seen.append(i.rule_id)
        return Step(Outcome.RESOLVED)

    Loop(watch).run([issue("OBS-004"), issue("SEC-002"), issue("BLD-001")])

    assert seen == ["SEC-002", "BLD-001", "OBS-004"]


# --------------------------------------------------------------------------
# retry budget
# --------------------------------------------------------------------------


def test_a_resolved_issue_is_attempted_once():
    result = Loop(always(Outcome.RESOLVED)).run([issue("SEC-002")])

    assert len(result.attempts) == 1
    assert result.resolved == ("SEC-002",)
    assert result.review == ()
    assert result.cleared


def test_an_issue_resolving_on_the_third_try_is_not_sent_for_review():
    result = Loop(resolves_on(3)).run([issue("SEC-002")])

    assert len(result.attempts) == 3
    assert [a.number for a in result.attempts] == [1, 2, 3]
    assert result.resolved == ("SEC-002",)
    assert result.review == ()


def test_the_budget_is_three_attempts_per_issue():
    result = Loop(always(Outcome.UNRESOLVED, "still failing")).run([issue("SEC-002")])

    assert len(result.attempts) == RETRIES == 3
    assert result.resolved == ()
    assert len(result.review) == 1


def test_the_budget_applies_to_each_issue_separately():
    issues = [issue("SEC-002"), issue("BLD-001"), issue("OBS-004")]

    result = Loop(always(Outcome.UNRESOLVED)).run(issues)

    assert len(result.attempts) == 3 * RETRIES
    for rule_id in ("SEC-002", "BLD-001", "OBS-004"):
        assert len([a for a in result.attempts if a.rule_id == rule_id]) == RETRIES


def test_the_budget_is_spent_once_per_run_not_once_per_cycle():
    """An issue already sent for review is never attempted again."""
    one = issue("SEC-002")

    result = Loop(always(Outcome.UNRESOLVED), requeue=lambda c: [one]).run([one])

    assert len(result.attempts) == RETRIES
    assert len(result.review) == 1


def test_a_blocked_issue_skips_the_rest_of_its_budget():
    """Retrying something that cannot be attempted wastes the budget."""
    result = Loop(always(Outcome.BLOCKED, "no template exists")).run([issue("SEC-002")])

    assert len(result.attempts) == 1
    assert len(result.review) == 1
    assert result.review[0].reason == "no template exists"


def test_the_retry_budget_is_configurable():
    result = Loop(always(Outcome.UNRESOLVED), retries=1).run([issue("SEC-002")])

    assert len(result.attempts) == 1


# --------------------------------------------------------------------------
# manual review record
# --------------------------------------------------------------------------


def test_an_exhausted_issue_is_flagged_with_location_and_reason():
    """Section 5.3 requires both, as structured output rather than a log line."""
    result = Loop(always(Outcome.UNRESOLVED, "helmet still missing")).run(
        [issue("SEC-002", file="src/app.js", line=12)]
    )

    review = result.review[0]
    assert review.rule_id == "SEC-002"
    assert review.priority == "P0"
    assert review.domain == "security"
    assert review.fix_type == "STATIC"
    assert review.attempts == RETRIES
    assert "helmet still missing" in review.reason
    assert review.locations[0]["file"] == "src/app.js"
    assert review.locations[0]["line"] == 12


def test_the_review_record_names_the_requirement():
    result = Loop(always(Outcome.UNRESOLVED)).run([issue("SEC-002")])

    assert result.review[0].requirement == get_rule("SEC-002").description


def test_review_reasons_distinguish_blocked_from_exhausted():
    blocked = Loop(always(Outcome.BLOCKED, "unsupported")).run([issue("SEC-002")])
    spent = Loop(always(Outcome.UNRESOLVED)).run([issue("SEC-002")])

    assert "unsupported" in blocked.review[0].reason
    assert "after 3 attempt" in spent.review[0].reason


def test_the_loop_continues_past_an_exhausted_issue():
    """Section 5.3 requires the run to carry on rather than halt."""
    issues = [issue("SEC-002"), issue("BLD-001"), issue("OBS-004")]

    def stubborn(i, n):
        return Step(Outcome.UNRESOLVED) if i.rule_id == "SEC-002" else Step(Outcome.RESOLVED)

    result = Loop(stubborn).run(issues)

    assert set(result.resolved) == {"BLD-001", "OBS-004"}
    assert [r.rule_id for r in result.review] == ["SEC-002"]


# --------------------------------------------------------------------------
# iteration ceiling
# --------------------------------------------------------------------------


def fresh_issues():
    """A re-audit that keeps surfacing different rules each cycle.

    It has to be a different rule each time, since the budget is spent per rule
    for the whole run. Handing back a rule already sent for review is the
    stalled case, not the ceiling case, and it is tested separately below.
    """
    pool = [r.rule_id for r in ALL_RULES]
    made = {"n": 0}

    def requeue(cycle: Cycle):
        made["n"] += 1
        return [issue(pool[made["n"] % len(pool)])]

    return requeue


def test_the_ceiling_stops_a_run_that_keeps_finding_new_work():
    result = Loop(always(Outcome.UNRESOLVED), requeue=fresh_issues()).run(
        [issue("SEC-002")]
    )

    assert result.stopped is Stop.CEILING
    assert result.iterations == CEILING == 5


def test_the_ceiling_is_configurable():
    result = Loop(always(Outcome.UNRESOLVED), requeue=fresh_issues(), ceiling=2).run(
        [issue("SEC-002")]
    )

    assert result.iterations == 2
    assert result.stopped is Stop.CEILING


def test_a_run_with_no_requeue_is_a_single_cycle():
    result = Loop(always(Outcome.RESOLVED)).run([issue("SEC-002")])

    assert result.iterations == 1
    assert result.stopped is Stop.CLEARED


def test_the_run_stops_once_the_requeue_has_nothing_new():
    result = Loop(always(Outcome.RESOLVED), requeue=lambda c: []).run([issue("SEC-002")])

    assert result.iterations == 1
    assert result.stopped is Stop.CLEARED


def test_a_cycle_that_can_attempt_nothing_stops_the_run():
    """Spinning to the ceiling doing no work would read as a hang."""
    calls = {"n": 0}

    def requeue(cycle):
        calls["n"] += 1
        return [issue("SEC-002")]

    result = Loop(always(Outcome.UNRESOLVED), requeue=requeue).run([issue("SEC-002")])

    assert result.iterations == 1
    assert calls["n"] == 1


# --------------------------------------------------------------------------
# boundedness
# --------------------------------------------------------------------------


def test_a_resolver_that_always_fails_still_terminates():
    """The adversarial case. The run must end without any cooperation."""
    issues = [issue(r.rule_id) for r in ALL_RULES[:12]]

    result = Loop(always(Outcome.UNRESOLVED), requeue=fresh_issues()).run(issues)

    assert result.iterations <= CEILING
    assert len(result.review) >= len(issues)
    assert result.stopped in (Stop.CEILING, Stop.CLEARED, Stop.STALLED)


def test_total_attempts_cannot_exceed_the_stated_bound():
    """ceiling times queue size times retries is the worst case, and it holds."""
    issues = [issue(r.rule_id) for r in ALL_RULES[:10]]

    result = Loop(always(Outcome.UNRESOLVED), requeue=fresh_issues()).run(issues)

    worst = CEILING * (len(issues) + CEILING) * RETRIES
    assert len(result.attempts) <= worst


def test_an_empty_queue_does_nothing_and_stops():
    result = Loop(always(Outcome.RESOLVED)).run([])

    assert result.attempts == ()
    assert result.review == ()
    assert result.iterations == 1


def test_a_resolve_step_that_raises_does_not_stop_the_run():
    """A defect below the controller must not abandon the remaining issues."""
    def boom(i, n):
        if i.rule_id == "SEC-002":
            raise RuntimeError("subprocess died")
        return Step(Outcome.RESOLVED)

    result = Loop(boom).run([issue("SEC-002"), issue("BLD-001")])

    assert result.resolved == ("BLD-001",)
    assert len(result.review) == 1
    assert "subprocess died" in result.review[0].reason


def test_a_requeue_that_raises_ends_the_run_cleanly():
    def boom(cycle):
        raise RuntimeError("re-audit failed")

    result = Loop(always(Outcome.RESOLVED), requeue=boom).run([issue("SEC-002")])

    assert result.resolved == ("SEC-002",)
    assert result.iterations == 1


def test_a_resolve_step_returning_the_wrong_type_is_rejected():
    with pytest.raises(LoopError):
        Loop(lambda i, n: "resolved").run([issue("SEC-002")])


@pytest.mark.parametrize("retries,ceiling", [(0, 5), (3, 0), (-1, 5), (3, -2)])
def test_impossible_bounds_are_refused(retries: int, ceiling: int):
    with pytest.raises(LoopError):
        Loop(always(Outcome.RESOLVED), retries=retries, ceiling=ceiling)


# --------------------------------------------------------------------------
# output shape
# --------------------------------------------------------------------------


def test_the_run_reports_every_cycle():
    result = Loop(resolves_on(2)).run([issue("SEC-002"), issue("BLD-001")])

    cycle = result.cycles[0]
    assert cycle.number == 1
    assert cycle.queued == 2
    assert cycle.attempted == 4
    assert set(cycle.resolved) == {"SEC-002", "BLD-001"}


def test_the_run_serialises_for_a_caller():
    result = Loop(always(Outcome.UNRESOLVED)).run([issue("SEC-002")])
    payload = result.to_dict()

    assert set(payload) == {
        "iterations", "stopped", "resolved", "manual_review", "cycles", "attempts",
    }
    assert payload["manual_review"][0]["rule_id"] == "SEC-002"
    assert payload["attempts"][0]["outcome"] == "unresolved"


def test_attempts_record_their_number_and_outcome():
    result = Loop(resolves_on(2)).run([issue("SEC-002")])

    assert [(a.number, a.outcome) for a in result.attempts] == [
        (1, Outcome.UNRESOLVED),
        (2, Outcome.RESOLVED),
    ]


def test_summary_reads_as_one_line():
    result = Loop(always(Outcome.RESOLVED)).run([issue("SEC-002")])

    assert "1 iteration" in result.summary()
    assert "1 resolved" in result.summary()


# --------------------------------------------------------------------------
# against a real audit
# --------------------------------------------------------------------------


def test_the_controller_consumes_a_real_audit_report():
    report = run(SAMPLE)

    result = Loop(always(Outcome.RESOLVED)).run(report.issues)

    assert len(result.resolved) == len(report.issues)
    assert result.cleared


def test_a_real_report_that_never_resolves_is_fully_reviewed():
    report = run(SAMPLE)

    result = Loop(always(Outcome.UNRESOLVED, "no fix logic yet")).run(report.issues)

    assert len(result.review) == len(report.issues)
    assert len(result.attempts) == len(report.issues) * RETRIES
    assert all(r.locations for r in result.review)


def test_blockers_are_worked_before_anything_else():
    report = run(SAMPLE)
    seen: list[str] = []

    def watch(i, n):
        seen.append(i.priority.value)
        return Step(Outcome.RESOLVED)

    Loop(watch).run(report.issues)

    assert seen[: len(report.blockers)] == ["P0"] * len(report.blockers)
