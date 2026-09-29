"""The live-agent harness prepares fair sessions and scores them from disk."""

from __future__ import annotations

import json

from agents import claims, issues_text, normal_hash, outcomes, prep, score


def test_claims_reads_fixed_and_not_fixed_lines() -> None:
    text = "Done.\nFIXED: SEC-001\n- NOT FIXED: STR-002\n* fixed: api-003\nSomething else"
    assert claims(text) == {"SEC-001": True, "STR-002": False, "API-003": True}


def test_outcomes_separates_fixed_failing_and_broken() -> None:
    before = {"A-001": {"status": "fail"}, "B-002": {"status": "fail"},
              "C-003": {"status": "pass"}, "_score": {"score": 10}}
    after = {"A-001": {"status": "pass"}, "B-002": {"status": "fail"},
             "C-003": {"status": "fail"}, "_score": {"score": 20}}
    assert outcomes(before, after) == {"fixed": ["A-001"], "still_failing": ["B-002"],
                                       "newly_broken": ["C-003"]}


def test_normal_hash_ignores_whitespace_only() -> None:
    assert normal_hash(b"a  b\n\tc") == normal_hash(b"a b c")
    assert normal_hash(b"a b c") != normal_hash(b"a b d")


def test_issues_text_lists_only_failing_rules() -> None:
    before = {"SEC-001": {"status": "fail", "requirement": "helmet must be registered",
                          "detail": "no helmet", "files": ["src/app.js"]},
              "OBS-001": {"status": "pass"}, "_score": {"score": 5}}
    text = issues_text(before)
    assert "[SEC-001] Helmet must be registered" in text and "OBS-001" not in text


def test_prep_and_score_an_untouched_session(tmp_path) -> None:
    work = prep("node_express_api", "B", 1, tmp_path)
    meta = json.loads((work / "harness.json").read_text(encoding="utf-8"))
    assert meta["failing"] and "prodpilot_fix_instruction" in meta["prompt"]
    (work / "agent_report.txt").write_text("FIXED: " + meta["failing"][0], encoding="utf-8")
    found = score(work)
    assert found["outcomes"].get("fixed", []) == []
    assert found["overclaims"] == [meta["failing"][0]]
    assert found["diff"]["files"] == [] and found["diff"]["history_rewritten"] is False
