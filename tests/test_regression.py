"""Every sample project is judged exactly as 1.0.0 judged it.

The record in tests/baseline/v1_0_0.json was written from the v1.0.0 tag by
tests/regress.py. Differences are allowed only where tests/baseline/allowed.json
names them, with the reason.
"""

from __future__ import annotations

import json

from regress import BASELINE, allowed, differences, snapshot, unexplained


def test_every_sample_matches_the_1_0_0_baseline() -> None:
    stored = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = snapshot()
    assert len(current) == 22
    assert unexplained(differences(stored, current), allowed()) == []


def test_no_documented_difference_touches_a_verdict_score_or_decision() -> None:
    for prefix in allowed():
        part = prefix.strip("/").split("/")
        assert len(part) >= 2 and part[1] == "contracts", prefix
