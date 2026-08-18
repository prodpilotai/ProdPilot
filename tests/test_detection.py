"""Detection and blueprint tests for Phase 1 module 1.2.

Exercises detect_stack against the committed sample projects, against the two
malformed manifest cases, and confirms each supported stack loads its own
blueprint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot.blueprint import (
    NODE_EXPRESS_BLUEPRINT,
    REACT_VITE_BLUEPRINT,
    Stack,
    get_blueprint,
    supported_stacks,
)
from prodpilot.detection import DetectionError, detect_stack

SAMPLES = Path(__file__).parent / "samples"


def test_node_express_sample_is_detected():
    result = detect_stack(SAMPLES / "node_express_api")

    assert result.stack is Stack.NODE_EXPRESS
    assert result.is_supported
    assert "express" in result.evidence.matched_packages
    assert result.evidence.manifest_found
    assert result.evidence.manifest_readable


def test_react_vite_sample_is_detected():
    result = detect_stack(SAMPLES / "react_vite_app")

    assert result.stack is Stack.REACT_VITE
    assert result.is_supported
    assert "react" in result.evidence.matched_packages
    assert "react-dom" in result.evidence.matched_packages
    assert "vite.config.js" in result.evidence.config_files_found


def test_project_without_manifest_is_unrecognized():
    result = detect_stack(SAMPLES / "unrecognized_python_service")

    assert result.stack is Stack.UNRECOGNIZED
    assert not result.is_supported
    assert result.blueprint() is None
    assert "package.json" in result.reason


def test_project_matching_both_stacks_fails_closed():
    """A single root declaring express and react with vite must not be guessed."""
    result = detect_stack(SAMPLES / "ambiguous_fullstack")

    assert result.stack is Stack.UNRECOGNIZED
    assert result.blueprint() is None
    assert "ambiguous" in result.reason


def test_each_supported_sample_loads_its_own_blueprint():
    node = detect_stack(SAMPLES / "node_express_api")
    react = detect_stack(SAMPLES / "react_vite_app")

    assert node.blueprint() is NODE_EXPRESS_BLUEPRINT
    assert react.blueprint() is REACT_VITE_BLUEPRINT
    assert node.blueprint() is not react.blueprint()


def test_react_without_vite_is_unrecognized(tmp_path: Path):
    """Only React with Vite is in scope, so React alone must not match."""
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"}}),
        encoding="utf-8",
    )

    result = detect_stack(tmp_path)

    assert result.stack is Stack.UNRECOGNIZED
    assert "vite" in result.reason


def test_malformed_manifest_is_unrecognized_not_an_exception(tmp_path: Path):
    (tmp_path / "package.json").write_text("{ not valid json", encoding="utf-8")

    result = detect_stack(tmp_path)

    assert result.stack is Stack.UNRECOGNIZED
    assert result.evidence.manifest_found
    assert not result.evidence.manifest_readable


def test_missing_path_raises_detection_error(tmp_path: Path):
    with pytest.raises(DetectionError):
        detect_stack(tmp_path / "does-not-exist")


def test_file_path_raises_detection_error(tmp_path: Path):
    target = tmp_path / "package.json"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(DetectionError):
        detect_stack(target)


def test_blueprints_cover_only_the_supported_stacks():
    assert set(supported_stacks()) == {Stack.NODE_EXPRESS, Stack.REACT_VITE}
    assert get_blueprint(Stack.UNRECOGNIZED) is None


def test_blueprint_items_have_unique_ids():
    for blueprint in (NODE_EXPRESS_BLUEPRINT, REACT_VITE_BLUEPRINT):
        ids = [
            item.item_id
            for group in (
                blueprint.required_files,
                blueprint.required_configs,
                blueprint.required_code_patterns,
            )
            for item in group
        ]
        assert len(ids) == len(set(ids))
        assert blueprint.item_count == len(ids)
