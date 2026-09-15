"""STATIC fix template tests for Phase 3 module 3.2.

Three things are proven here. Every STATIC rule in the frozen store has a
template, every template renders the Section 5.2 contract correctly, and the
output is byte identical on repeated calls, since zero variance is the
requirement rather than a description.
"""

from __future__ import annotations

import re

import pytest

from prodpilot.audit import RuleResult, run
from prodpilot.findings import Finding, Status
from prodpilot.loop import Loop, Outcome, Step
from prodpilot.rules import ALL_RULES, FixType, get_rule
from prodpilot.templates import (
    ANCHORS,
    CONSTRAINT,
    TEMPLATES,
    Action,
    Instruction,
    Template,
    TemplateError,
    covers,
    get,
    render,
    resolver,
    static_rules,
)

STATIC_IDS = sorted(r.rule_id for r in ALL_RULES if r.fix_type is FixType.STATIC)


def issue(rule_id: str, file: str = "src/server.js", line: int = 4) -> RuleResult:
    """A failing RuleResult for a real rule from the frozen store."""
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    hit = Finding(rule_id, Status.FAIL, file, line, f"{rule_id} is not satisfied")
    return RuleResult(rule=rule, status=Status.FAIL, evidence=(hit,))


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def test_every_static_rule_has_a_template():
    """The frozen store is the scope, so none of it may be left unwired."""
    wired = set(TEMPLATES)
    target = {r.rule_id for r in static_rules()}

    assert wired == target, f"missing {sorted(target - wired)}, extra {sorted(wired - target)}"
    assert len(target) == 28


def test_no_rule_of_another_fix_type_is_wired_here():
    """DYNAMIC-PARAMETRIC and DYNAMIC-DELEGATED belong to 3.3 and 3.4."""
    for rule_id in TEMPLATES:
        assert get_rule(rule_id).fix_type is FixType.STATIC, rule_id


def test_template_ids_match_the_frozen_store():
    """The store already names the template each rule expects."""
    for rule_id, template in TEMPLATES.items():
        assert template.template_id == get_rule(rule_id).fix_template_id, rule_id


def test_covers_answers_for_both_cases():
    assert covers("SEC-002")
    assert not covers("STR-001")
    assert not covers("NOPE-000")


# --------------------------------------------------------------------------
# template shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", STATIC_IDS)
def test_each_template_is_complete(rule_id: str):
    template = get(rule_id)

    assert template.content.strip(), f"{rule_id} has empty content"
    assert template.rationale.strip(), f"{rule_id} has no rationale"
    assert isinstance(template.action, Action)


@pytest.mark.parametrize("rule_id", STATIC_IDS)
def test_each_anchor_is_a_known_locator(rule_id: str):
    """An anchor a caller cannot resolve is worse than no anchor."""
    template = get(rule_id)

    if template.action is Action.CREATE_FILE:
        assert template.anchor == ""
    else:
        assert template.anchor in ANCHORS, f"{rule_id} uses unknown anchor {template.anchor}"


def test_create_file_templates_carry_their_own_path():
    for rule_id, template in TEMPLATES.items():
        if template.action is Action.CREATE_FILE:
            assert template.path, rule_id


def test_a_template_with_an_unknown_anchor_is_refused():
    with pytest.raises(TemplateError):
        Template("tpl.x", Action.INSERT_AFTER, "nowhere:at-all", "x", "y")


def test_a_create_file_template_without_a_path_is_refused():
    with pytest.raises(TemplateError):
        Template("tpl.x", Action.CREATE_FILE, "", "x", "y")


def test_no_template_content_carries_a_placeholder():
    """A template is the finished fix, not a form to fill in."""
    markers = ("TODO", "FIXME", "<your", "PLACEHOLDER", "{{", "XXX")
    for rule_id, template in TEMPLATES.items():
        for marker in markers:
            assert marker not in template.content, f"{rule_id} contains {marker}"


# --------------------------------------------------------------------------
# zero variance
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", STATIC_IDS)
def test_a_template_returns_the_same_bytes_every_time(rule_id: str):
    """Zero variance by construction is the requirement, so it is measured."""
    first = get(rule_id).content
    second = get(rule_id).content

    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


@pytest.mark.parametrize("rule_id", STATIC_IDS)
def test_rendering_the_same_issue_twice_is_byte_identical(rule_id: str):
    one = issue(rule_id)

    first = render(one).to_dict()
    second = render(one).to_dict()

    assert first == second
    for key in first:
        assert str(first[key]).encode("utf-8") == str(second[key]).encode("utf-8")


def test_two_projects_get_the_same_content_for_the_same_rule():
    """Identical for every codebase is what STATIC means."""
    a = render(issue("SEC-002", file="src/server.js"))
    b = render(issue("SEC-002", file="app/index.js"))

    assert a.content == b.content
    assert a.anchor == b.anchor
    assert a.rationale == b.rationale
    assert a.file_path != b.file_path


def test_templates_are_frozen_records():
    with pytest.raises(Exception):
        get("SEC-002").content = "something else"


# --------------------------------------------------------------------------
# the Section 5.2 contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", STATIC_IDS)
def test_render_produces_every_contract_field(rule_id: str):
    payload = render(issue(rule_id)).to_dict()

    assert set(payload) == {
        "rule_id", "action", "file_path", "anchor", "content", "rationale", "constraint",
        "packages",
    }
    assert payload["rule_id"] == rule_id
    assert payload["action"] in {a.value for a in Action}
    assert payload["file_path"]
    assert payload["constraint"] == CONSTRAINT
    assert isinstance(payload["packages"], dict)


def test_the_constraint_keeps_the_wording_from_the_document():
    """Both sentences Section 5.2 gives, with the two conditions a retry needs."""
    assert CONSTRAINT.startswith("Apply exactly this change.")
    assert CONSTRAINT.endswith("Make no other modifications.")
    assert "already in place" in CONSTRAINT
    assert "package.json" in CONSTRAINT


@pytest.mark.parametrize("rule_id", ["SEC-002", "SEC-003", "SEC-004", "API-001",
                                     "OBS-002", "OBS-003"])
def test_content_that_requires_a_package_declares_it(rule_id: str):
    """A require with nothing in package.json crashes the service on start."""
    template = get(rule_id)
    needed = set(re.findall(r'require\("([^"]+)"\)', template.content))

    assert needed
    assert needed <= {name for name, _ in template.packages}
    assert all(version.startswith("^") for _, version in template.packages)


def test_content_that_requires_nothing_declares_nothing():
    for rule_id, template in TEMPLATES.items():
        if "require(" not in template.content:
            assert template.packages == (), rule_id


def test_the_helmet_and_cors_bindings_cannot_collide():
    """Each binds its module inside its own block, so an existing binding is shadowed."""
    for rule_id, name in (("SEC-002", "helmet"), ("SEC-003", "cors")):
        content = get(rule_id).content
        assert content.startswith("{\n") and content.endswith("}\n"), rule_id
        assert f'const {name} = require("{name}");' in content, rule_id


def test_the_cors_fix_replaces_the_existing_registration():
    """A second registration would leave the first answering with the old origin."""
    template = get("SEC-003")

    assert template.action is Action.REPLACE_BLOCK
    assert template.anchor == "express:cors-call"


def test_the_shutdown_handler_is_safe_without_a_bound_server():
    content = get("OBS-004").content

    assert 'typeof server === "undefined"' in content
    assert "server" in get("ENV-002").content.split("=")[0]


def test_a_code_rule_takes_its_path_from_the_audit_finding():
    rendered = render(issue("SEC-002", file="src/api/app.js"))

    assert rendered.file_path == "src/api/app.js"


def test_a_config_rule_uses_the_path_the_template_owns():
    """The finding names .gitignore anyway, but the template must not depend on it."""
    rendered = render(issue("GIT-002", file="somewhere/else.txt"))

    assert rendered.file_path == ".gitignore"


def test_rendering_a_non_static_rule_is_refused():
    """A misrouted issue must fail loudly rather than produce a wrong fix."""
    with pytest.raises(TemplateError):
        render(issue("STR-001"))


def test_asking_for_an_unknown_rule_is_refused():
    with pytest.raises(TemplateError):
        get("NOPE-000")


def test_a_code_rule_with_no_file_anywhere_is_refused():
    rule = get_rule("SEC-002")
    bare = RuleResult(rule=rule, status=Status.FAIL, evidence=())

    with pytest.raises(TemplateError):
        render(bare)


# --------------------------------------------------------------------------
# content sanity
# --------------------------------------------------------------------------


def test_middleware_registrations_target_the_app():
    for rule_id in ("SEC-002", "SEC-003", "API-001", "OBS-001", "OBS-002"):
        assert "app." in get(rule_id).content, rule_id


def test_the_cors_template_reads_the_origin_from_the_environment():
    """Section 4.1 names this as the STATIC example."""
    content = get("SEC-003").content

    assert "process.env" in content
    assert '"*"' not in content


def test_the_port_template_reads_the_port_from_the_environment():
    assert "process.env.PORT" in get("ENV-002").content


def test_the_error_handler_takes_four_arguments():
    """Express identifies an error handler by arity, so this matters."""
    assert "(err, req, res, next)" in get("API-003").content


def test_the_error_handler_leaks_no_stack_trace():
    content = get("API-003").content

    assert "err.stack" not in content
    assert "stack" not in content


def test_the_dockerfile_templates_name_an_unprivileged_user():
    assert get("SEC-001").content.strip() == "USER node"
    lines = get("SEC-005").content.strip().splitlines()
    assert lines[-1] == "USER nginx"
    # nginx cannot start as that user until its cache and pid file are its own.
    assert "chown -R nginx:nginx /var/cache/nginx" in lines[0]


def test_the_gitignore_templates_keep_the_env_example():
    """Excluding .env must not also exclude the template developers need."""
    for rule_id in ("SCR-001", "SCR-003", "GIT-001", "GIT-004"):
        assert "!.env.example" in get(rule_id).content, rule_id


def test_the_nginx_template_carries_everything_its_rules_require():
    content = get("BLD-009").content

    assert "try_files" in content and "index.html" in content
    assert "/health" in content
    assert "access_log" in content and "error_log" in content
    assert "Content-Security-Policy" in content


def test_the_engine_template_pins_node_twenty():
    assert '"node": ">=20 <21"' in get("BLD-005").content


# --------------------------------------------------------------------------
# against real audits
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample", ["node_express_insecure", "node_express_api", "react_vite_app"]
)
def test_every_static_issue_in_a_real_audit_renders(sample: str):
    report = run(f"tests/samples/{sample}")
    statics = [i for i in report.issues if i.rule.fix_type is FixType.STATIC]

    assert statics, f"{sample} raised no STATIC issues"
    for one in statics:
        rendered = render(one)
        assert rendered.rule_id == one.rule_id
        assert rendered.file_path
        assert rendered.content.strip()


def test_a_real_audit_renders_identically_on_a_second_run():
    report = run("tests/samples/node_express_insecure")
    statics = [i for i in report.issues if i.rule.fix_type is FixType.STATIC]

    first = [render(i).to_dict() for i in statics]
    second = [render(i).to_dict() for i in statics]

    assert first == second


# --------------------------------------------------------------------------
# the integration point for 3.5
# --------------------------------------------------------------------------


def test_the_resolver_reports_resolved_when_the_rule_passes_after_the_change():
    resolve = resolver(lambda instruction: True)

    step = resolve(issue("SEC-002"), 1)

    assert step.outcome is Outcome.RESOLVED


def test_the_resolver_reports_unresolved_when_the_rule_still_fails():
    resolve = resolver(lambda instruction: False)

    step = resolve(issue("SEC-002"), 1)

    assert step.outcome is Outcome.UNRESOLVED


def test_the_resolver_blocks_an_issue_another_module_owns():
    """Retrying a delegated rule here would only waste the budget."""
    resolve = resolver(lambda instruction: True)

    step = resolve(issue("STR-001"), 1)

    assert step.outcome is Outcome.BLOCKED
    assert "not STATIC" in step.detail


def test_the_resolver_absorbs_an_apply_step_that_raises():
    def boom(instruction):
        raise RuntimeError("the tool call failed")

    step = resolver(boom)(issue("SEC-002"), 1)

    assert step.outcome is Outcome.UNRESOLVED
    assert "the tool call failed" in step.detail


def test_the_resolver_receives_the_rendered_instruction():
    seen: list[Instruction] = []

    def capture(instruction):
        seen.append(instruction)
        return True

    resolver(capture)(issue("SEC-003"), 1)

    assert len(seen) == 1
    assert seen[0].rule_id == "SEC-003"
    assert "process.env" in seen[0].content


def test_the_resolver_drives_the_real_loop_controller():
    """Proves the shape 3.5 will wire actually plugs into 3.1 unchanged."""
    report = run("tests/samples/node_express_insecure")
    statics = [i for i in report.issues if i.rule.fix_type is FixType.STATIC]

    result = Loop(resolver(lambda instruction: True)).run(statics)

    assert len(result.resolved) == len(statics)
    assert result.review == ()
    assert result.cleared


def test_a_failing_apply_step_exhausts_the_budget_through_the_controller():
    result = Loop(resolver(lambda instruction: False)).run([issue("SEC-002")])

    assert len(result.attempts) == 3
    assert len(result.review) == 1
    assert result.review[0].rule_id == "SEC-002"


def test_the_resolver_signature_matches_the_controller_contract():
    resolve = resolver(lambda instruction: True)

    step = resolve(issue("SEC-002"), 2)

    assert isinstance(step, Step)
