"""Every sample project is judged exactly as 1.0.0 judged it.

The record in tests/baseline/v1_0_0.json was written from the v1.0.0 tag by
tests/regress.py. Differences are allowed only where tests/baseline/allowed.json
names them, as exact text substitutions in named fix contracts.
"""

from __future__ import annotations

import json
import re

from regress import BASELINE, allowed, differences, expected, snapshot


def test_every_sample_matches_the_1_0_0_baseline() -> None:
    stored = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = snapshot()
    assert len(current) == 22
    assert differences(expected(stored, allowed()), current) == []


def test_verdicts_scores_and_gate_decisions_match_exactly() -> None:
    """No documented difference may reach anything but a fix contract."""
    stored = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = snapshot()
    for name, was in stored.items():
        now = current[name]
        for key in ("stack", "detection", "score", "band", "rules", "blockers", "gate"):
            assert now[key] == was[key], f"{name}/{key}"
        assert set(now["contracts"]) == set(was["contracts"]), name
        for rule, contract in was["contracts"].items():
            assert now["contracts"][rule]["ok"] == contract["ok"], f"{name}/{rule}"


def test_documented_differences_only_change_a_node_version() -> None:
    for entry in allowed():
        assert entry["reason"]
        assert set(entry["rules"]) <= {"BLD-001", "BLD-003", "BLD-006", "BLD-007",
                                       "BLD-010", "BLD-012"}
        for old, new in entry["replace"]:
            assert re.sub(r"\d+", "N", old) == re.sub(r"\d+", "N", new), (old, new)
