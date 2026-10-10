"""The frozen core of ProdPilot 1.x.

Release 1.1 adds tooling around the system and changes nothing about how a
project is judged. These tests fail if any part of that judgement moves: the
ruleset, the shipped model and the scikit-learn version it was saved with, the
scoring weights and bands, the gate's conditions, or the five MCP tool names.

A change that trips one of them is not a test to update. It is a change to the
frozen core, which belongs in a new major version with a new model and a new
evaluation, not in a 1.x release.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from prodpilot import gate, scoring
from prodpilot.audit import BANDS, WEIGHTS
from prodpilot.blueprint import Stack
from prodpilot.rules import ALL_RULES, rules_for_stack
from prodpilot.server import (
    DEPLOY_TOOL_NAME,
    DETECT_STACK_TOOL_NAME,
    FIX_APPLIED_TOOL_NAME,
    FIX_INSTRUCTION_TOOL_NAME,
    PING_TOOL_NAME,
)

from interface import tools

REPO = Path(__file__).resolve().parents[1]

# Recorded from the v1.0.0 tag.
RULESET_SHA256 = "886310e99994bca0900b95d0a2d4508a5a6a4d9c6f4d2639010759ae3a079ad6"
MODEL_SHA256 = "74f08c40d4bcabfb25d2d723872cf05a081be7cd45b837596c54540c1ac8eef0"
OPERATING_POINT = 0.3685529155882552

TOOL_NAMES = {
    "prodpilot_ping",
    "prodpilot_detect_stack",
    "prodpilot_fix_instruction",
    "prodpilot_fix_applied",
    "prodpilot_deploy",
}


def canonical_ruleset() -> bytes:
    """The ruleset as one byte string: every field of every rule, in order."""
    return json.dumps([r.to_dict() for r in ALL_RULES], sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True).encode("ascii")


def test_the_ruleset_is_unchanged() -> None:
    assert len(ALL_RULES) == 50
    assert len(rules_for_stack(Stack.NODE_EXPRESS)) == 28
    assert len(rules_for_stack(Stack.REACT_VITE)) == 22
    assert hashlib.sha256(canonical_ruleset()).hexdigest() == RULESET_SHA256


def test_the_shipped_model_is_unchanged() -> None:
    digest = hashlib.sha256(scoring.ARTIFACT.read_bytes()).hexdigest()
    assert digest == MODEL_SHA256


def test_the_model_keeps_its_operating_point() -> None:
    assert scoring.load().threshold == OPERATING_POINT
    assert round(OPERATING_POINT, 3) == 0.369


def test_scikit_learn_stays_pinned_to_the_version_that_saved_the_model() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert "scikit-learn==1.9.0" in project["project"]["dependencies"]


def test_scoring_weights_and_band_edges_are_unchanged() -> None:
    assert {p.value: w for p, w in WEIGHTS.items()} == {
        "P0": 32, "P1": 16, "P2": 8, "P3": 4, "P4": 2, "P5": 1}
    assert [(floor, band.value) for floor, band in BANDS] == [
        (90, "Production Ready"), (70, "Nearly Ready"), (40, "Needs Work"), (0, "Not Ready")]


def test_the_gate_conditions_are_unchanged() -> None:
    assert gate.THRESHOLD == 90
    assert gate.VETO == frozenset({"node_express", "react_vite"})


def test_the_five_tool_names_are_unchanged() -> None:
    assert {PING_TOOL_NAME, DETECT_STACK_TOOL_NAME, FIX_INSTRUCTION_TOOL_NAME,
            FIX_APPLIED_TOOL_NAME, DEPLOY_TOOL_NAME} == TOOL_NAMES
    assert set(tools()) == TOOL_NAMES
