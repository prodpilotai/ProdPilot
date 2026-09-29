"""The model evidence counts decisions, resamples and rescores correctly."""

from __future__ import annotations

import numpy as np

from prodpilot.modelstats import SCHEMES, cells, measure, rescore, resample


def test_measure_counts_the_decision_at_the_cut() -> None:
    m = measure([1, 1, 0, 0, 0], [0.9, 0.2, 0.6, 0.1, 0.05], 0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 2)
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert 0.0 <= m["roc_auc"] <= 1.0 and "brier" in m


def test_measure_skips_ranking_with_one_class() -> None:
    m = measure([0, 0, 0], [0.1, 0.2, 0.3], 0.5, prob=False)
    assert "roc_auc" not in m and "brier" not in m


def test_resample_keeps_every_stratum_size() -> None:
    y = [1, 1, 0, 0, 0, 1]
    stack = ["a", "b", "a", "a", "b", "a"]
    groups = cells(list(range(6)), y, stack)
    pick = resample(groups, np.random.default_rng(1))
    assert len(pick) == 6
    for (label, s), members in groups.items():
        assert sum(1 for i in pick if y[i] == label and stack[i] == s) == len(members)


def test_rescore_reproduces_the_halving_score_and_differs_under_equal_weights() -> None:
    scored = [["SEC-001", "P0", "fail"], ["OBS-001", "P5", "pass"], ["API-001", "P4", "pass"],
              ["CON-001", "P2", "skipped"]]
    assert rescore(scored, SCHEMES["halving"]) == round(100 * 3 / 35)
    assert rescore(scored, SCHEMES["equal"]) == round(100 * 2 / 3)


def test_rescore_with_nothing_scored_is_zero() -> None:
    assert rescore([["X-1", "P0", "skipped"]], SCHEMES["linear"]) == 0
