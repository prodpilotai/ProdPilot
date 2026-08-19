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
    Domain,
    Priority,
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


def test_vite_detected_from_typescript_config_without_the_package():
    """Vite is often only a transitive install, so the config file also counts."""
    result = detect_stack(SAMPLES / "react_vite_ts_config")

    assert result.stack is Stack.REACT_VITE
    assert result.is_supported
    assert result.blueprint() is REACT_VITE_BLUEPRINT
    assert "vite.config.ts" in result.evidence.config_files_found
    assert "vite" not in result.evidence.declared_dependencies


def test_vite_without_react_is_unrecognized():
    """Vite drives other frameworks too, so Vite alone must not match."""
    result = detect_stack(SAMPLES / "vite_without_react")

    assert result.stack is Stack.UNRECOGNIZED
    assert result.blueprint() is None
    assert "react" in result.reason


def test_manifest_that_is_not_an_object_is_unrecognized(tmp_path: Path):
    """A syntactically valid manifest can still be the wrong shape."""
    (tmp_path / "package.json").write_text(json.dumps(["express"]), encoding="utf-8")

    result = detect_stack(tmp_path)

    assert result.stack is Stack.UNRECOGNIZED
    assert result.evidence.manifest_found
    assert not result.evidence.manifest_readable


def test_dependency_sections_that_are_not_objects_are_ignored(tmp_path: Path):
    """A malformed dependencies block must not raise, it must simply not match."""
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": "express", "devDependencies": ["vite"]}),
        encoding="utf-8",
    )

    result = detect_stack(tmp_path)

    assert result.stack is Stack.UNRECOGNIZED
    assert result.evidence.manifest_readable
    assert result.evidence.declared_dependencies == ()


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


def test_every_rule_domain_is_covered_or_declared_not_applicable():
    """Section 4 lists nine domains. Each must be accounted for per stack.

    Guards against a domain being silently dropped from a blueprint. A domain
    that cannot apply to a stack has to say so rather than just be absent.
    """
    for blueprint in (NODE_EXPRESS_BLUEPRINT, REACT_VITE_BLUEPRINT):
        accounted = blueprint.covered_domains | set(blueprint.not_applicable_domains)
        assert accounted == set(Domain), (
            f"{blueprint.stack.value} does not account for "
            f"{sorted(d.value for d in set(Domain) - accounted)}"
        )
        assert not (blueprint.covered_domains & set(blueprint.not_applicable_domains))


def test_every_priority_tier_is_represented_across_the_ruleset():
    """P0 through P5 all exist in Section 4, so the blueprints must use them."""
    used = {
        item.priority
        for blueprint in (NODE_EXPRESS_BLUEPRINT, REACT_VITE_BLUEPRINT)
        for item in blueprint.items
    }
    assert used == set(Priority)


def test_ruleset_meets_the_declared_v1_size():
    """Section 4 freezes v1 at 40 or more rules across the two stacks."""
    total = NODE_EXPRESS_BLUEPRINT.item_count + REACT_VITE_BLUEPRINT.item_count
    assert total >= 40


def test_blueprint_item_ids_are_namespaced_by_stack():
    """Phase 2 will key rules off item_id, so ids must not collide across stacks."""
    node_ids = {item.item_id for item in NODE_EXPRESS_BLUEPRINT.items}
    react_ids = {item.item_id for item in REACT_VITE_BLUEPRINT.items}

    assert not node_ids & react_ids
    assert all(i.startswith("node.") for i in node_ids)
    assert all(i.startswith("react.") for i in react_ids)


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
