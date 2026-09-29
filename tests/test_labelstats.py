"""The label study's intervals and stage grouping behave as documented."""

from __future__ import annotations

from prodpilot.labelstats import gap, normal_quantile, origin, row, stage_of, wilson


def test_origin_recovers_the_source_repository() -> None:
    assert origin("hutuxuhui__jackblog-api-express_git-002", "GIT-002") == \
        "hutuxuhui/jackblog-api-express"
    assert origin("a__my_repo_name_scr-002", "SCR-002") == "a/my_repo_name"


def test_normal_quantile_matches_known_values() -> None:
    assert round(normal_quantile(0.975), 3) == 1.960
    assert round(normal_quantile(0.9995), 3) == 3.291


def test_bonferroni_flag_is_stricter() -> None:
    failing = [{"label": 0}] * 30 + [{"label": 1}] * 2
    passing = [{"label": 1}] * 4 + [{"label": 0}] * 8
    r = row("node_express", "X-001", failing, passing, normal_quantile(1 - 0.025 / 50))
    assert r["excludes_zero"] is True and r["excludes_zero_corrected"] is False


def test_wilson_matches_known_values() -> None:
    p, low, high = wilson(0, 10)
    assert p == 0.0 and low == 0.0 and round(high, 4) == 0.2775
    p, low, high = wilson(5, 10)
    assert p == 0.5 and round(low, 4) == 0.2366 and round(high, 4) == 0.7634


def test_gap_is_positive_when_the_first_rate_is_higher() -> None:
    d, low, high = gap(9, 10, 1, 10)
    assert d == 0.8 and low > 0 and high <= 1


def test_row_flags_only_intervals_that_exclude_zero() -> None:
    failing = [{"label": 0}] * 20
    passing = [{"label": 1}] * 15 + [{"label": 0}] * 5
    assert row("node_express", "SEC-001", failing, passing)["excludes_zero"] is True
    same = [{"label": 1}, {"label": 0}]
    assert row("node_express", "SEC-001", same, same)["excludes_zero"] is False


def test_stage_of_groups_the_stored_details() -> None:
    assert stage_of("deploy monitoring", "x: failed, unclassified") == "Render reported the deploy failed"
    assert stage_of("deploy monitoring", "still building after 600s") == "timed out while Render was building"
    assert stage_of("post-deploy smoke test", "health probe: no healthy response") == \
        "went live, health path never answered"
