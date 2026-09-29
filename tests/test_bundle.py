"""The artefact bundle exports the frozen ruleset and, with the data present, a manifest."""

from __future__ import annotations

import csv
import json

import pytest

from bundle import DATA, manifest, ruleset


def test_ruleset_export_holds_all_fifty_rules(tmp_path) -> None:
    assert ruleset(tmp_path) == 50
    rules = json.loads((tmp_path / "ruleset.json").read_text(encoding="utf-8"))
    assert {r["fix_type"] for r in rules} == {"STATIC", "DYNAMIC-PARAMETRIC", "DYNAMIC-DELEGATED"}
    assert sum(1 for r in rules if r["fix_type"] == "DYNAMIC-DELEGATED") == 6


@pytest.mark.skipif(not (DATA / "labels.jsonl").exists(), reason="the labelled dataset is not present")
def test_manifest_lists_every_labelled_project(tmp_path) -> None:
    rows = manifest(tmp_path)
    with (tmp_path / "manifest.csv").open(encoding="utf-8") as fh:
        read = list(csv.DictReader(fh))
    assert rows == len(read) == 684
    assert {r["split"] for r in read} <= {"train", "held_out", "not in matrix"}
    assert sum(1 for r in read if r["split"] == "held_out") == 169
