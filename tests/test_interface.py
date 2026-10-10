"""The 1.0.0 interface is still served.

The snapshot in tests/baseline/interface_v1_0_0.json was taken from the v1.0.0
tag. A 1.x release may add tools inputs, commands and flags, but only optional
ones, and may not remove, rename or change the default of anything 1.0.0 had.
"""

from __future__ import annotations

import copy
import json

from interface import SNAPSHOT, compatible, snapshot


def stored() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_current_interface_serves_everything_1_0_0_did() -> None:
    assert compatible(stored(), snapshot()) == []


def test_the_check_catches_a_removed_option() -> None:
    old = stored()
    new = copy.deepcopy(old)
    new["commands"]["doctor"]["params"] = []
    assert any("--project" in p or "project" in p for p in compatible(old, new))


def test_the_check_catches_a_new_required_tool_input() -> None:
    old = stored()
    new = copy.deepcopy(old)
    schema = new["tools"]["prodpilot_fix_instruction"]["input_schema"]
    schema["properties"]["mode"] = {"type": "string"}
    schema["required"] = [*schema.get("required", []), "mode"]
    assert compatible(old, new) == [
        "tool prodpilot_fix_instruction: new input mode is required"]


def test_the_check_catches_a_changed_default() -> None:
    old = stored()
    new = copy.deepcopy(old)
    new["tools"]["prodpilot_deploy"]["input_schema"]["properties"]["branch"]["default"] = "dev"
    assert compatible(old, new) == ["tool prodpilot_deploy: input branch changed its default"]
