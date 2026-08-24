"""Rule schema and rule store tests for Phase 2 module 2.1.

Same approach as the module 1.2 tests: structural guards that fail loudly if a
domain, a priority tier or a required field is dropped, plus checks that the
rule store and the blueprints cannot drift apart.

Nothing here runs a check. Check execution is modules 2.2 and 2.3.
"""

from __future__ import annotations

from collections import Counter

import pytest

from prodpilot import blueprint as blueprint_module
from prodpilot.blueprint import (
    NODE_EXPRESS_BLUEPRINT,
    REACT_VITE_BLUEPRINT,
    Domain,
    Priority,
    Stack,
)
from prodpilot.rules import (
    ALL_RULES,
    NODE_EXPRESS_RULES,
    REACT_VITE_RULES,
    REFERENCE_FIELD_FOR_FIX_TYPE,
    REFERENCE_FIELDS,
    CheckType,
    FixType,
    Rule,
    RuleDefinitionError,
    Scope,
    blueprint_item_ids,
    determinism_ratio,
    get_rule,
    rules_by_priority,
    rules_for_stack,
)

BLUEPRINTS = {
    Stack.NODE_EXPRESS: NODE_EXPRESS_BLUEPRINT,
    Stack.REACT_VITE: REACT_VITE_BLUEPRINT,
}


def blueprint_items_by_id(stack: Stack) -> dict[str, object]:
    return {item.item_id: item for item in BLUEPRINTS[stack].items}


# --------------------------------------------------------------------------
# Vocabulary reuse
# --------------------------------------------------------------------------


def test_domain_and_priority_come_from_the_blueprint_module():
    """The rule store must not define a second copy of these vocabularies."""
    assert Domain is blueprint_module.Domain
    assert Priority is blueprint_module.Priority
    assert Stack is blueprint_module.Stack


def test_every_section_four_field_is_present_on_a_rule():
    """Section 4 lists the fields a rule carries. None may be missing."""
    required = {
        "rule_id",
        "domain",
        "priority",
        "scope",
        "check_type",
        "fix_type",
    }
    for rule in ALL_RULES:
        for name in required:
            assert getattr(rule, name) is not None, f"{rule.rule_id} missing {name}"


def test_enum_values_match_the_solution_document_spelling():
    assert {s.value for s in Scope} == {"file", "cross_file"}
    assert {c.value for c in CheckType} == {"ast", "file_existence", "entropy_scan"}
    assert {f.value for f in FixType} == {
        "STATIC",
        "DYNAMIC-PARAMETRIC",
        "DYNAMIC-DELEGATED",
    }


# --------------------------------------------------------------------------
# Rule store integrity
# --------------------------------------------------------------------------


def test_rule_ids_are_unique():
    ids = [rule.rule_id for rule in ALL_RULES]
    assert len(ids) == len(set(ids))


def test_rule_id_prefix_matches_its_domain():
    """An identifier has to be readable on its own, without a lookup."""
    prefixes = {
        Domain.SECURITY: "SEC",
        Domain.SECRETS: "SCR",
        Domain.ENVIRONMENT: "ENV",
        Domain.BUILD: "BLD",
        Domain.CONNECTIVITY: "CON",
        Domain.API: "API",
        Domain.STRUCTURE: "STR",
        Domain.OBSERVABILITY: "OBS",
        Domain.GIT_HYGIENE: "GIT",
    }
    for rule in ALL_RULES:
        assert rule.rule_id.startswith(prefixes[rule.domain] + "-"), rule.rule_id


def test_ruleset_meets_the_declared_v1_size():
    """Section 4 freezes v1 at 40 or more rules across the two stacks."""
    assert len(ALL_RULES) >= 40


def test_rule_count_matches_the_blueprint_count():
    """The ruleset derives from the blueprints, it is not invented separately."""
    assert len(NODE_EXPRESS_RULES) == NODE_EXPRESS_BLUEPRINT.item_count
    assert len(REACT_VITE_RULES) == REACT_VITE_BLUEPRINT.item_count
    assert len(ALL_RULES) == (
        NODE_EXPRESS_BLUEPRINT.item_count + REACT_VITE_BLUEPRINT.item_count
    )


# --------------------------------------------------------------------------
# Rules against blueprints
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stack", [Stack.NODE_EXPRESS, Stack.REACT_VITE])
def test_rules_map_one_to_one_onto_blueprint_items(stack: Stack):
    """Every blueprint requirement is enforced by exactly one rule."""
    referenced = [rule.blueprint_item_id for rule in rules_for_stack(stack)]

    assert len(referenced) == len(set(referenced)), "a blueprint item is claimed twice"
    assert set(referenced) == blueprint_item_ids(stack)


@pytest.mark.parametrize("stack", [Stack.NODE_EXPRESS, Stack.REACT_VITE])
def test_rule_domain_and_priority_match_its_blueprint_item(stack: Stack):
    """Guards against the two vocabularies drifting apart.

    A rule that reclassifies its own domain or priority would silently
    contradict the blueprint the audit reports against.
    """
    items = blueprint_items_by_id(stack)
    for rule in rules_for_stack(stack):
        item = items[rule.blueprint_item_id]
        assert rule.domain is item.domain, rule.rule_id
        assert rule.priority is item.priority, rule.rule_id


@pytest.mark.parametrize("stack", [Stack.NODE_EXPRESS, Stack.REACT_VITE])
def test_rules_carry_the_stack_they_belong_to(stack: Stack):
    for rule in rules_for_stack(stack):
        assert rule.stack is stack


def test_no_rule_targets_a_domain_its_stack_declares_inapplicable():
    """React declares connectivity and API inapplicable, so no rule may use them."""
    for stack, bp in BLUEPRINTS.items():
        excluded = set(bp.not_applicable_domains)
        for rule in rules_for_stack(stack):
            assert rule.domain not in excluded, f"{rule.rule_id} uses an excluded domain"


# --------------------------------------------------------------------------
# Fix type and its reference field
# --------------------------------------------------------------------------


def test_each_rule_populates_exactly_its_fix_type_reference_field():
    """STATIC needs a template, PARAMETRIC an extraction, DELEGATED a constraint."""
    for rule in ALL_RULES:
        expected = REFERENCE_FIELD_FOR_FIX_TYPE[rule.fix_type]
        assert getattr(rule, expected), f"{rule.rule_id} has no {expected}"
        for other in REFERENCE_FIELDS:
            if other != expected:
                assert getattr(rule, other) is None, (
                    f"{rule.rule_id} sets {other} as well as {expected}"
                )


def test_reference_identifiers_are_prefixed_by_fix_type():
    prefixes = {
        FixType.STATIC: "tpl.",
        FixType.DYNAMIC_PARAMETRIC: "ext.",
        FixType.DYNAMIC_DELEGATED: "con.",
    }
    for rule in ALL_RULES:
        assert rule.reference_value.startswith(prefixes[rule.fix_type]), rule.rule_id


def test_reference_identifiers_are_unique():
    """Phase 3 resolves these, so two rules must not claim the same one."""
    refs = [rule.reference_value for rule in ALL_RULES]
    assert len(refs) == len(set(refs))


def test_a_rule_missing_its_reference_field_is_rejected():
    with pytest.raises(RuleDefinitionError):
        Rule(
            rule_id="SEC-999",
            blueprint_item_id="node.code.helmet_registered",
            stack=Stack.NODE_EXPRESS,
            domain=Domain.SECURITY,
            priority=Priority.P0,
            scope=Scope.FILE,
            check_type=CheckType.AST,
            fix_type=FixType.STATIC,
            description="missing its template reference",
        )


def test_a_rule_setting_the_wrong_reference_field_is_rejected():
    with pytest.raises(RuleDefinitionError):
        Rule(
            rule_id="SEC-998",
            blueprint_item_id="node.code.helmet_registered",
            stack=Stack.NODE_EXPRESS,
            domain=Domain.SECURITY,
            priority=Priority.P0,
            scope=Scope.FILE,
            check_type=CheckType.AST,
            fix_type=FixType.STATIC,
            description="a static rule must not carry a constraint",
            fix_template_id="tpl.node.helmet_registered",
            constraint_template="con.node.helmet_registered",
        )


# --------------------------------------------------------------------------
# Coverage guards
# --------------------------------------------------------------------------


def test_every_domain_is_covered_by_at_least_one_rule():
    covered = {rule.domain for rule in ALL_RULES}
    assert covered == set(Domain)


def test_every_priority_tier_is_used():
    used = {rule.priority for rule in ALL_RULES}
    assert used == set(Priority)


def test_every_check_type_is_used():
    used = {rule.check_type for rule in ALL_RULES}
    assert used == set(CheckType)


def test_every_fix_type_is_used():
    used = {rule.fix_type for rule in ALL_RULES}
    assert used == set(FixType)


def test_both_scopes_are_used():
    used = {rule.scope for rule in ALL_RULES}
    assert used == set(Scope)


# --------------------------------------------------------------------------
# Section 4.1 proportions
# --------------------------------------------------------------------------


def test_delegated_fixes_are_a_small_minority():
    """Section 4.1 states most rules are STATIC or DYNAMIC-PARAMETRIC."""
    counts = Counter(rule.fix_type for rule in ALL_RULES)
    delegated = counts[FixType.DYNAMIC_DELEGATED]
    deterministic = counts[FixType.STATIC] + counts[FixType.DYNAMIC_PARAMETRIC]

    assert deterministic > delegated
    assert delegated / len(ALL_RULES) < 0.25


def test_determinism_ratio_is_measured_not_asserted():
    counts = Counter(rule.fix_type for rule in ALL_RULES)
    expected = (
        counts[FixType.STATIC] + counts[FixType.DYNAMIC_PARAMETRIC]
    ) / len(ALL_RULES)

    assert determinism_ratio() == pytest.approx(expected)
    assert 0.0 < determinism_ratio() <= 1.0


def test_structural_rules_are_delegated():
    """Section 4.1 names the structure domain as the delegated case."""
    for rule in ALL_RULES:
        if rule.domain is Domain.STRUCTURE:
            assert rule.fix_type is FixType.DYNAMIC_DELEGATED, rule.rule_id


def test_is_deterministic_agrees_with_fix_type():
    for rule in ALL_RULES:
        assert rule.is_deterministic == (rule.fix_type is not FixType.DYNAMIC_DELEGATED)


# --------------------------------------------------------------------------
# Store accessors
# --------------------------------------------------------------------------


def test_unrecognized_stack_has_no_rules():
    """No blueprint means nothing to audit against."""
    assert rules_for_stack(Stack.UNRECOGNIZED) == ()
    assert blueprint_item_ids(Stack.UNRECOGNIZED) == frozenset()


def test_get_rule_finds_a_known_rule_and_returns_none_otherwise():
    known = ALL_RULES[0]

    assert get_rule(known.rule_id) is known
    assert get_rule("NOPE-000") is None


def test_rules_by_priority_orders_p0_first():
    ordered = rules_by_priority(Stack.NODE_EXPRESS)

    assert len(ordered) == len(NODE_EXPRESS_RULES)
    positions = [list(Priority).index(rule.priority) for rule in ordered]
    assert positions == sorted(positions)
    assert ordered[0].priority is Priority.P0


def test_to_dict_round_trips_every_field():
    rule = ALL_RULES[0]
    payload = rule.to_dict()

    assert payload["rule_id"] == rule.rule_id
    assert payload["blueprint_item_id"] == rule.blueprint_item_id
    assert payload["domain"] == rule.domain.value
    assert payload["priority"] == rule.priority.value
    assert payload["scope"] == rule.scope.value
    assert payload["check_type"] == rule.check_type.value
    assert payload["fix_type"] == rule.fix_type.value
    assert set(REFERENCE_FIELDS) <= set(payload)


def test_rules_are_immutable():
    """The store is frozen data. A caller must not be able to edit it."""
    with pytest.raises(Exception):
        ALL_RULES[0].priority = Priority.P5
