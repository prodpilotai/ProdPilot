"""The revision figures render from their JSON inputs."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("matplotlib")

from figures import main  # noqa: E402


def test_figures_render_from_minimal_inputs(tmp_path) -> None:
    shares = {t: {"count": 1, "share": 1 / 3} for t in
              ("STATIC", "DYNAMIC-PARAMETRIC", "DYNAMIC-DELEGATED")}
    (tmp_path / "rq2.json").write_text(json.dumps({
        "by_type": shares, "by_stack": {"node_express": {"by_type": shares}}}), encoding="utf-8")
    curve = {"fpr": [0, 0.5, 1], "tpr": [0, 0.8, 1], "precision": [1, 0.5, 0.2],
             "recall": [0, 0.8, 1]}
    part = {"rows": 10, "positive": 2, "tp": 1, "fp": 1, "fn": 1, "tn": 7,
            "precision": 0.5, "recall": 0.5, "roc_auc": 0.8, "pr_auc": 0.5}
    names = ("D", "E", "build_only", "audit_score", "rule_gate")
    parts = ("overall", "react_vite", "node_express")
    model = {"held_out_metrics": {n: {p: dict(part) for p in parts} for n in names},
             "curves": {n: curve for n in names},
             "bootstrap": {"ci": {n: {p: {"roc_auc": {"low": 0.7, "high": 0.9},
                                          "pr_auc": {"low": 0.4, "high": 0.6}} for p in parts}
                                  for n in names}}}
    (tmp_path / "model.json").write_text(json.dumps(model), encoding="utf-8")
    out = tmp_path / "out"
    main(str(tmp_path), str(out))
    for name in ("rq2_fix_types.pdf", "model_curves.pdf", "model_forest.pdf", "model_confusion.pdf"):
        assert (out / name).stat().st_size > 1000
