"""Score computation and reporting tests for Phase 2 module 2.4.

Covers the aggregation from findings to one verdict per rule, the weighted
score, the four bands from Section 6, and the report shape the Phase 3 loop
consumes. The last section verifies Phase 2's own exit criteria against the
committed fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prodpilot import astchecks, filechecks
from prodpilot.audit import (
    BANDS,
    WEIGHTS,
    Band,
    Report,
    RuleResult,
    band_of,
    collect,
    run,
    score_of,
    verdict,
)
from prodpilot.blueprint import Domain, Priority, Stack
from prodpilot.findings import Finding, Status
from prodpilot.rules import ALL_RULES, FixType, get_rule

SAMPLES = Path(__file__).resolve().parent / "samples"
HARD_NODE = SAMPLES / "node_express_hardened"
READY_REACT = SAMPLES / "react_vite_ready"
BARE_NODE = SAMPLES / "node_express_api"
LEAKY = SAMPLES / "node_express_secrets"


def result(rule_id: str, status: Status) -> RuleResult:
    return RuleResult(rule=get_rule(rule_id), status=status)


# --------------------------------------------------------------------------
# the shared findings module
# --------------------------------------------------------------------------


def test_all_three_families_share_one_finding_type():
    """The whole engine must hand module 2.4 the same shape."""
    from prodpilot import findings

    assert astchecks.Finding is findings.Finding
    assert filechecks.Finding is findings.Finding
    assert astchecks.Status is findings.Status is filechecks.Status


def test_astchecks_still_re_exports_the_names():
    """Moving them must not break an existing import."""
    from prodpilot.astchecks import Finding as F, Status as S

    assert F is Finding and S is Status


# --------------------------------------------------------------------------
# bands
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, Band.NOT_READY), (39, Band.NOT_READY),
        (40, Band.NEEDS_WORK), (69, Band.NEEDS_WORK),
        (70, Band.NEARLY_READY), (89, Band.NEARLY_READY),
        (90, Band.PRODUCTION_READY), (100, Band.PRODUCTION_READY),
    ],
)
def test_bands_match_section_six(score: int, expected: Band):
    assert band_of(score) is expected


def test_band_names_are_exactly_the_document_wording():
    assert [b.value for _, b in BANDS] == [
        "Production Ready", "Nearly Ready", "Needs Work", "Not Ready"
    ]


# --------------------------------------------------------------------------
# verdict per rule
# --------------------------------------------------------------------------


def test_a_rule_fails_if_any_file_fails_it():
    hits = [
        Finding("SEC-002", Status.PASS, "a.js"),
        Finding("SEC-002", Status.FAIL, "b.js"),
        Finding("SEC-002", Status.SKIPPED, "c.js"),
    ]
    assert verdict(hits) is Status.FAIL


def test_unparsed_outranks_pass_but_not_fail():
    assert verdict([Finding("X", Status.PASS, "a"), Finding("X", Status.UNPARSED, "b")]) is Status.UNPARSED
    assert verdict([Finding("X", Status.FAIL, "a"), Finding("X", Status.UNPARSED, "b")]) is Status.FAIL


def test_a_rule_with_no_findings_is_skipped():
    assert verdict([]) is Status.SKIPPED


def test_one_verdict_per_rule_not_per_file():
    """A rule reported in four files must weigh once, not four times."""
    hits = [Finding("SEC-002", Status.FAIL, f"f{i}.js") for i in range(4)]
    results = collect(hits, Stack.NODE_EXPRESS)

    sec = [r for r in results if r.rule_id == "SEC-002"]
    assert len(sec) == 1
    assert len(sec[0].evidence) == 4


def test_collect_covers_every_rule_for_the_stack():
    """A check that silently stopped running must show as skipped, not vanish."""
    results = collect([], Stack.NODE_EXPRESS)
    expected = {r.rule_id for r in ALL_RULES if r.stack is Stack.NODE_EXPRESS}

    assert {r.rule_id for r in results} == expected
    assert all(r.status is Status.SKIPPED for r in results)


def test_a_finding_for_an_unknown_rule_is_rejected():
    with pytest.raises(ValueError):
        collect([Finding("NOPE-000", Status.FAIL, "a.js")], Stack.NODE_EXPRESS)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def test_weights_halve_at_each_tier():
    assert [WEIGHTS[p] for p in Priority] == [32, 16, 8, 4, 2, 1]


def test_one_critical_outweighs_every_low_rule():
    """P0 means critical, so it has to cost more than the whole P5 tier."""
    p5_total = sum(WEIGHTS[r.priority] for r in ALL_RULES if r.priority is Priority.P5)

    assert WEIGHTS[Priority.P0] > p5_total


def test_all_passing_scores_one_hundred():
    results = [result("SEC-002", Status.PASS), result("BLD-001", Status.PASS)]

    assert score_of(results) == 100


def test_all_failing_scores_zero():
    results = [result("SEC-002", Status.FAIL), result("BLD-001", Status.FAIL)]

    assert score_of(results) == 0


def test_score_is_weighted_by_priority():
    """Failing one P0 must cost more than failing one P5."""
    lose_p0 = [result("SEC-002", Status.FAIL), result("GIT-001", Status.PASS)]
    lose_p5 = [result("SEC-002", Status.PASS), result("GIT-001", Status.FAIL)]

    assert score_of(lose_p0) < score_of(lose_p5)


def test_skipped_rules_leave_the_score_untouched():
    """A rule that cannot apply must not drag a project down."""
    without = [result("SEC-002", Status.PASS)]
    with_skip = [result("SEC-002", Status.PASS), result("GIT-001", Status.SKIPPED)]

    assert score_of(without) == score_of(with_skip) == 100


def test_unparsed_counts_against_the_score():
    """Otherwise an unreadable file becomes the cheapest way to score well."""
    parsed = [result("SEC-002", Status.PASS), result("BLD-001", Status.PASS)]
    broken = [result("SEC-002", Status.PASS), result("BLD-001", Status.UNPARSED)]

    assert score_of(broken) < score_of(parsed)


def test_no_assessable_rules_scores_zero():
    assert score_of([]) == 0
    assert score_of([result("SEC-002", Status.SKIPPED)]) == 0


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def test_hardened_project_scores_above_a_bare_one():
    assert run(HARD_NODE).score > run(BARE_NODE).score


def test_score_is_stable_across_runs():
    """Phase 2 requires a stable score, so the same input must not drift."""
    scores = {run(BARE_NODE).score for _ in range(3)}

    assert len(scores) == 1


def test_issues_are_ordered_worst_first():
    """This is the queue Phase 3 consumes, and Section 5.3 works P0 first."""
    order = {p: i for i, p in enumerate(Priority)}
    issues = run(BARE_NODE).issues

    positions = [order[r.priority] for r in issues]
    assert positions == sorted(positions)


def test_blockers_are_the_failed_critical_rules():
    report = run(BARE_NODE)

    assert report.blockers
    assert all(r.priority is Priority.P0 for r in report.blockers)
    assert set(report.blockers) <= set(report.issues)


def test_every_issue_carries_what_phase_three_needs():
    """rule_id, status, priority, domain, fix type and somewhere to look."""
    for issue in run(BARE_NODE).to_dict()["issues"]:
        assert issue["rule_id"]
        assert issue["status"] in ("fail", "unparsed")
        assert issue["priority"] in {p.value for p in Priority}
        assert issue["domain"] in {d.value for d in Domain}
        assert issue["fix_type"] in {f.value for f in FixType}
        assert issue["requirement"]
        assert issue["detail"]


def test_report_dictionary_has_the_aggregate_fields():
    payload = run(BARE_NODE).to_dict()

    assert set(payload) == {
        "project", "stack", "detection", "score", "band", "counts",
        "failures_by_priority", "failures_by_domain", "issues",
        "passed_rules", "skipped_rules",
    }
    assert payload["band"] in {b.value for b in Band}
    assert payload["counts"]["assessed"] > 0


def test_counts_add_up():
    report = run(BARE_NODE)
    counts = report.to_dict()["counts"]

    assert counts["passed"] + counts["failed"] == counts["assessed"]
    assert counts["skipped"] == len(report.skipped)


def test_unrecognized_project_reports_zero_without_crashing():
    report = run(SAMPLES / "unrecognized_python_service")

    assert report.stack is Stack.UNRECOGNIZED
    assert report.score == 0
    assert report.band is Band.NOT_READY
    assert report.results == ()


def test_run_rejects_a_file_path(tmp_path: Path):
    target = tmp_path / "thing.js"
    target.write_text("const a = 1;", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        run(target)


def test_summary_reads_as_one_line():
    line = run(BARE_NODE).summary()

    assert "/100" in line
    assert "issue" in line


# --------------------------------------------------------------------------
# Phase 2 exit criteria
# --------------------------------------------------------------------------


def test_broken_project_flags_its_seeded_issues_with_correct_metadata():
    """Phase 2's stated exit criterion, against a committed broken fixture."""
    report = run(SAMPLES / "node_express_insecure")

    failed = {r.rule_id for r in report.issues}
    # Seeded deliberately: helmet after the routes, a hardcoded CORS origin,
    # the error handler registered too early, a database call in a controller
    # and another in a route handler.
    for rule_id in ("SEC-002", "SEC-003", "API-003", "STR-001", "STR-002"):
        assert rule_id in failed, rule_id

    for issue in report.issues:
        rule = get_rule(issue.rule_id)
        assert issue.priority is rule.priority
        assert issue.domain is rule.domain
        assert issue.fix_type is rule.fix_type


def test_planted_secret_is_reported_as_a_critical_issue():
    report = run(LEAKY)
    secrets = [r for r in report.issues if r.rule_id == "SCR-002"]

    assert len(secrets) == 1
    assert secrets[0].priority is Priority.P0
    assert secrets[0] in report.blockers


def test_a_well_built_project_reaches_the_top_band():
    report = run(READY_REACT)

    assert report.band is Band.PRODUCTION_READY
    assert report.blockers == ()


def test_every_rule_in_a_report_exists_in_the_frozen_store():
    for sample in (HARD_NODE, BARE_NODE, READY_REACT):
        for r in run(sample).results:
            assert get_rule(r.rule_id) is r.rule
