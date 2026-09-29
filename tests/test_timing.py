"""The timing script measures the audit and one repair cycle on a real sample."""

from __future__ import annotations

from timing import measure, spread


def test_spread_summarises_times() -> None:
    assert spread([3.0, 1.0, 2.0]) == {"samples": 3, "median": 2.0, "min": 1.0, "max": 3.0}
    assert spread([]) == {}


def test_measure_times_the_audit_and_a_cycle(tmp_path) -> None:
    found = measure("react_vite_ready", tmp_path)
    assert found["sample"] == "react_vite_ready"
    assert found["audit_seconds"] >= 0
    assert found["issues"] >= 1 and found["cycle_seconds"] >= 0 and found["contracts"] >= 1
