"""RQ2's summary counts violations by fix type and groups refusals honestly."""

from __future__ import annotations

from prodpilot.violations import kind, reason, summarise


def test_kind_separates_preconditions_from_ambiguity() -> None:
    assert kind("the Dockerfile declares no base image to build from") == "precondition"
    assert kind("N lockfiles are present, so the package manager is unclear") == "ambiguity"
    assert kind("no inline connection string was found to move") == "disagreement"
    assert kind("no entry point is declared and none of the conventional names exist") == \
        "missing input"
    assert kind("something new") == "other"


def test_reason_drops_rule_id_candidates_and_counts() -> None:
    a = reason("BLD-001: 3 candidate entry points exist and package.json names none. "
               "Candidates: src/a.js, src/b.js")
    b = reason("BLD-004: 2 candidate entry points exist and package.json names none. "
               "Candidates: x.js")
    assert a == b == "N candidate entry points exist and package.json names none"


def fail(rule: str, kind: str, priority: str, **extra) -> dict:
    return {"rule": rule, "type": kind, "priority": priority, "domain": "build", **extra}


def test_summary_counts_types_stacks_and_refusals() -> None:
    rows = [
        {"name": "a", "stack": "node_express", "score": 10, "fails": [
            fail("SEC-001", "STATIC", "P0"),
            fail("BLD-001", "DYNAMIC-PARAMETRIC", "P1", extracted=True),
            fail("STR-001", "DYNAMIC-DELEGATED", "P3")]},
        {"name": "b", "stack": "react_vite", "score": 50, "fails": [
            fail("BLD-012", "DYNAMIC-PARAMETRIC", "P1", extracted=False,
                 refusal="the Dockerfile declares no base image to build from")]},
        {"name": "c", "error": "RecursionError: too deep"},
    ]
    s = summarise(rows)
    assert s["repos_found"] == 3 and s["repos_audited"] == 2
    assert s["violations"] == 4
    assert s["by_type"]["STATIC"]["count"] == 1
    assert s["deterministic_share"] == 0.75
    assert s["repos_with_delegated"] == 1
    assert s["single_rule_repos"] == 1
    assert s["extraction"]["parametric_instances"] == 2
    assert s["extraction"]["refused"] == 1
    assert s["not_audited"][0]["name"] == "c"


def test_priority_weighting_favours_critical_rules() -> None:
    rows = [{"name": "a", "stack": "node_express", "score": 0, "fails": [
        fail("SEC-001", "STATIC", "P0"), fail("STR-001", "DYNAMIC-DELEGATED", "P5")]}]
    s = summarise(rows)
    assert s["deterministic_share"] == 0.5
    assert s["priority_weighted"]["deterministic_share"] == round(32 / 33, 4)
