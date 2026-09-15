"""DYNAMIC-PARAMETRIC extraction tests for Phase 3 module 3.3.

Two things carry the weight here. Every routine derives real values from a real
fixture, and every routine that can be made ambiguous refuses to choose and says
what it found instead. The second matters more: Section 4.1 requires ambiguity
to be surfaced rather than guessed, and a wrong environment variable name would
produce a fix that satisfies its own checker while leaving the application
broken.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from prodpilot.audit import RuleResult, run
from prodpilot.findings import Finding, Status
from prodpilot.loop import Loop, Outcome, Step
from prodpilot.rules import ALL_RULES, FixType, get_rule
from prodpilot.templates import CONSTRAINT, LINE, Action
from prodpilot.extraction import (
    SHAPES,
    moved,
    ROUTINES,
    SHAPES,
    SPECS,
    Extract,
    ExtractError,
    Source,
    covers,
    driver,
    entry_point,
    extract,
    load,
    manager,
    name_for,
    parametric_rules,
    prefix_of,
    render,
    resolver,
    screaming,
    version_prefix,
)

SAMPLES = Path(__file__).resolve().parent / "samples"
DP_IDS = sorted(r.rule_id for r in ALL_RULES if r.fix_type is FixType.DYNAMIC_PARAMETRIC)


def issue(rule_id: str, file: str = "src/server.js", line: int = 3) -> RuleResult:
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    hit = Finding(rule_id, Status.FAIL, file, line, f"{rule_id} is not satisfied")
    return RuleResult(rule=rule, status=Status.FAIL, evidence=(hit,))


def src_for(sample: str) -> Source:
    return load(SAMPLES / sample)


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def test_every_parametric_rule_has_a_routine():
    target = {r.rule_id for r in parametric_rules()}

    assert set(ROUTINES) == target, f"missing {sorted(target - set(ROUTINES))}"
    assert set(SHAPES) == target
    assert len(target) == 16


def test_no_rule_of_another_fix_type_is_wired_here():
    for rule_id in ROUTINES:
        assert get_rule(rule_id).fix_type is FixType.DYNAMIC_PARAMETRIC, rule_id


def test_specs_match_the_frozen_store():
    """The store already names the extraction each rule expects."""
    for rule_id, spec in SPECS.items():
        assert get_rule(rule_id).extraction_spec == spec, rule_id
    assert set(SPECS) == set(ROUTINES)


def test_covers_answers_for_both_cases():
    assert covers("SCR-002")
    assert not covers("SEC-002")
    assert not covers("NOPE-000")


# --------------------------------------------------------------------------
# the naming routine Section 4.1 names as the example
# --------------------------------------------------------------------------


def test_a_secret_takes_the_name_it_is_already_bound_to():
    src = src_for("node_express_secrets")

    got = extract(src, issue("SCR-002"))

    assert got.ok
    assert got.values["key"] == "GITHUB_TOKEN"


def test_a_camel_case_binding_becomes_screaming_snake():
    assert screaming("apiSecret") == "API_SECRET"
    assert screaming("db_url") == "DB_URL"
    assert screaming("GITHUB_TOKEN") == "GITHUB_TOKEN"


def test_a_unanimous_prefix_is_adopted():
    assert prefix_of({"APP_PORT", "APP_HOST"}) == "APP_"


def test_a_split_prefix_is_not_adopted():
    """Two conventions mean there is no convention to follow."""
    assert prefix_of({"APP_PORT", "SVC_HOST"}) == ""


def test_a_single_key_is_not_treated_as_a_convention():
    assert prefix_of({"APP_PORT"}) == ""


# --------------------------------------------------------------------------
# fail closed, proven on real ambiguous fixtures
# --------------------------------------------------------------------------


def test_two_lockfiles_refuse_to_name_a_package_manager():
    got = manager(src_for("amb_two_lockfiles"))

    assert not got.ok
    assert set(got.candidates) == {"package-lock.json", "yarn.lock"}
    assert "unclear" in got.reason


def test_several_entry_points_refuse_to_pick_one():
    got = entry_point(src_for("amb_two_entries"))

    assert not got.ok
    assert len(got.candidates) == 3
    assert all(c.endswith(".js") for c in got.candidates)


def test_two_database_drivers_refuse_to_pick_a_pool():
    got = driver(src_for("amb_two_drivers"))

    assert not got.ok
    assert set(got.candidates) == {"mysql", "postgres"}


def test_an_unnamed_secret_refuses_to_invent_a_key():
    """No binding means no name to derive, so nothing may be guessed."""
    got = extract(src_for("amb_unnamed_secret"), issue("SCR-002"))

    assert not got.ok
    assert "not bound to a named identifier" in got.reason


def test_two_mount_roots_refuse_to_derive_one_prefix():
    got = version_prefix(src_for("amb_two_roots"), issue("API-002"))

    assert not got.ok
    assert set(got.candidates) == {"/orders", "/billing"}


def test_a_refused_extraction_never_carries_values():
    for sample, rule_id in [
        ("amb_two_entries", "BLD-004"),
        ("amb_two_drivers", "CON-002"),
        ("amb_unnamed_secret", "SCR-002"),
        ("amb_two_roots", "API-002"),
    ]:
        got = extract(src_for(sample), issue(rule_id))
        assert not got.ok, rule_id
        assert got.values == {}, rule_id
        assert got.reason, rule_id


def test_a_refusal_reports_candidates_where_there_are_any():
    got = extract(src_for("amb_two_entries"), issue("BLD-004"))

    assert got.candidates
    assert got.to_dict()["candidates"] == list(got.candidates)


# --------------------------------------------------------------------------
# clean extraction
# --------------------------------------------------------------------------


def test_the_entry_point_comes_from_the_manifest():
    got = entry_point(src_for("node_express_api"))

    assert got.ok
    assert got.values["entry"] == "src/server.js"


def test_a_single_lockfile_names_the_manager():
    got = manager(src_for("node_express_ready"))

    assert got.ok
    assert got.values["manager"] == "npm"


def test_a_dockerfile_is_built_from_the_projects_own_values():
    got = extract(src_for("node_express_api"), issue("BLD-001"))

    assert got.ok
    assert got.values["entry"] == "src/server.js"
    assert got.values["node"] == "20"


def test_the_node_version_is_read_from_the_engines_field():
    got = extract(src_for("node_express_ready"), issue("BLD-001"))

    assert got.ok
    assert got.values["node"] == "20"


def test_a_version_prefix_is_derived_from_the_existing_mount():
    got = version_prefix(src_for("node_express_api"), issue("API-002"))

    assert got.ok
    assert got.values["prefix"] == "/orders/v1"


def test_the_env_template_lists_only_undeclared_keys():
    """node_express_secure reads two keys and declares none of them."""
    got = extract(src_for("node_express_secure"), issue("ENV-001"))

    assert got.ok
    assert int(got.values["count"]) == 2
    assert "CORS_ORIGIN=" in got.values["keys"]
    assert "PORT=" in got.values["keys"]


def test_a_project_declaring_every_key_it_reads_needs_no_change():
    """Nothing to extract is a refusal, not an empty fix."""
    got = extract(src_for("node_express_hardened"), issue("ENV-001"))

    assert not got.ok
    assert "already declared" in got.reason


def test_extraction_is_deterministic():
    src = src_for("node_express_secrets")
    one = issue("SCR-002")

    assert extract(src, one).to_dict() == extract(src, one).to_dict()


# --------------------------------------------------------------------------
# rendering the Section 5.2 contract
# --------------------------------------------------------------------------


def test_render_produces_every_contract_field():
    src = src_for("node_express_api")

    payload = render(src, issue("BLD-001")).to_dict()

    assert set(payload) == {
        "rule_id", "action", "file_path", "anchor", "content", "rationale", "constraint",
        "packages", "env",
    }
    assert payload["rule_id"] == "BLD-001"
    assert payload["constraint"] == CONSTRAINT


def test_a_rendered_dockerfile_carries_the_projects_entry_point():
    """The content is ProdPilot determined, the parameters are the project's."""
    rendered = render(src_for("node_express_api"), issue("BLD-001"))

    assert "src/server.js" in rendered.content
    assert "node:20-alpine" in rendered.content
    assert rendered.action is Action.CREATE_FILE
    assert rendered.file_path == "Dockerfile"


def test_content_follows_the_project_rather_than_a_fixed_template(tmp_path: Path):
    """This is what separates a parametric fix from a static one."""
    (tmp_path / "package.json").write_text(
        '{"main": "lib/boot.js", "engines": {"node": ">=22"},'
        ' "dependencies": {"express": "1"}}',
        encoding="utf-8",
    )
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "boot.js").write_text("const a = 1;", encoding="utf-8")

    mine = render(load(tmp_path), issue("BLD-001"))
    other = render(src_for("node_express_api"), issue("BLD-001"))

    assert "lib/boot.js" in mine.content
    assert "node:22-alpine" in mine.content
    assert mine.content != other.content


def test_rendering_a_refused_extraction_raises():
    """The caller must handle a refusal as manual review, not as a fix."""
    with pytest.raises(ExtractError):
        render(src_for("amb_two_entries"), issue("BLD-004"))


def test_extracting_a_rule_of_another_fix_type_raises():
    with pytest.raises(ExtractError):
        extract(src_for("node_express_api"), issue("SEC-002"))


def test_extracting_an_unknown_rule_raises():
    rule = get_rule("BLD-001")
    fake = RuleResult(rule=rule, status=Status.FAIL, evidence=())
    object.__setattr__(fake.rule, "rule_id", "NOPE-000")
    try:
        with pytest.raises(ExtractError):
            extract(src_for("node_express_api"), fake)
    finally:
        object.__setattr__(fake.rule, "rule_id", "BLD-001")


# --------------------------------------------------------------------------
# against real audits
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample", ["node_express_api", "node_express_secrets", "react_vite_app"]
)
def test_every_parametric_issue_in_a_real_audit_is_answered(sample: str):
    """Answered means resolved or refused with a reason. Never silent."""
    src = src_for(sample)
    report = run(SAMPLES / sample)
    dp = [i for i in report.issues if i.rule.fix_type is FixType.DYNAMIC_PARAMETRIC]

    assert dp, f"{sample} raised no DYNAMIC-PARAMETRIC issues"
    for one in dp:
        got = extract(src, one)
        if got.ok:
            assert got.values
            assert render(src, one).content.strip()
        else:
            assert got.reason.strip(), one.rule_id


def test_a_real_audit_extracts_identically_on_a_second_run():
    src = src_for("node_express_api")
    report = run(SAMPLES / "node_express_api")
    dp = [i for i in report.issues if i.rule.fix_type is FixType.DYNAMIC_PARAMETRIC]

    first = [extract(src, i).to_dict() for i in dp]
    second = [extract(src, i).to_dict() for i in dp]

    assert first == second


# --------------------------------------------------------------------------
# the integration point for 3.5
# --------------------------------------------------------------------------


def test_the_resolver_reports_resolved_when_the_rule_passes_after_the_change():
    resolve = resolver(lambda i: True, SAMPLES / "node_express_api")

    step = resolve(issue("BLD-001"), 1)

    assert step.outcome is Outcome.RESOLVED


def test_the_resolver_reports_unresolved_when_the_rule_still_fails():
    resolve = resolver(lambda i: False, SAMPLES / "node_express_api")

    step = resolve(issue("BLD-001"), 1)

    assert step.outcome is Outcome.UNRESOLVED


def test_the_resolver_blocks_an_ambiguous_issue_and_names_the_candidates():
    """Blocked, not unresolved, since retrying re-reads the same codebase."""
    resolve = resolver(lambda i: True, SAMPLES / "amb_two_entries")

    step = resolve(issue("BLD-004"), 1)

    assert step.outcome is Outcome.BLOCKED
    assert "Candidates:" in step.detail
    assert "server.js" in step.detail


def test_the_resolver_blocks_an_issue_another_module_owns():
    resolve = resolver(lambda i: True, SAMPLES / "node_express_api")

    step = resolve(issue("SEC-002"), 1)

    assert step.outcome is Outcome.BLOCKED
    assert "not DYNAMIC-PARAMETRIC" in step.detail


def test_the_resolver_absorbs_an_apply_step_that_raises():
    def boom(instruction):
        raise RuntimeError("the tool call failed")

    step = resolver(boom, SAMPLES / "node_express_api")(issue("BLD-001"), 1)

    assert step.outcome is Outcome.UNRESOLVED
    assert "the tool call failed" in step.detail


def test_the_resolver_receives_the_rendered_instruction():
    seen = []

    def capture(instruction):
        seen.append(instruction)
        return True

    resolver(capture, SAMPLES / "node_express_api")(issue("BLD-001"), 1)

    assert len(seen) == 1
    assert seen[0].rule_id == "BLD-001"
    assert "src/server.js" in seen[0].content


def test_an_ambiguous_issue_costs_one_attempt_not_three():
    """Blocking rather than retrying is what keeps the budget for real work."""
    resolve = resolver(lambda i: True, SAMPLES / "amb_two_entries")

    result = Loop(resolve).run([issue("BLD-004")])

    assert len(result.attempts) == 1
    assert len(result.review) == 1
    assert "Candidates:" in result.review[0].reason


def test_the_resolver_drives_the_real_loop_controller():
    src = src_for("node_express_api")
    report = run(SAMPLES / "node_express_api")
    dp = [i for i in report.issues if i.rule.fix_type is FixType.DYNAMIC_PARAMETRIC
          and extract(src, i).ok]
    resolve = resolver(lambda i: True, SAMPLES / "node_express_api")

    result = Loop(resolve).run(dp)

    assert len(result.resolved) == len(dp)
    assert result.review == ()


def test_the_resolver_signature_matches_the_controller_contract():
    resolve = resolver(lambda i: True, SAMPLES / "node_express_api")

    assert isinstance(resolve(issue("BLD-001"), 2), Step)


def test_a_missing_project_is_blocked_rather_than_raised():
    resolve = resolver(lambda i: True, SAMPLES / "no-such-project")

    step = resolve(issue("BLD-001"), 1)

    assert step.outcome is Outcome.BLOCKED
    assert "cannot read the project" in step.detail


# --------------------------------------------------------------------------
# rewriting a known line rather than appending anything
# --------------------------------------------------------------------------


def original(sample: str, file_path: str, anchor: str) -> str:
    number = int(anchor[len(LINE):])
    lines = (SAMPLES / sample / file_path).read_text(encoding="utf-8").splitlines()
    return lines[number - 1]


def test_no_parametric_fix_appends_a_bare_expression():
    """A replacement at the end of a file is an append, which fixed nothing."""
    for rule_id, shape in SHAPES.items():
        if shape.action is Action.REPLACE_BLOCK:
            assert shape.anchor != "file:end", rule_id


def test_a_version_prefix_rewrites_the_line_that_mounts_the_route():
    src = src_for("node_express_api")
    got = version_prefix(src, issue("API-002"))

    rendered = render(src, issue("API-002"))

    assert rendered.action is Action.REPLACE_BLOCK
    assert rendered.anchor.startswith(LINE)
    before = original("node_express_api", rendered.file_path, rendered.anchor)
    assert got.values["current"] in before
    assert "/orders/v1" in rendered.content
    assert rendered.content.rstrip("\n") != before


def test_a_moved_secret_never_travels_in_the_contract():
    """The rewritten line reads the environment, and the literal is nowhere in it."""
    rendered = render(src_for("node_express_secrets"), issue("SCR-002"))

    before = original("node_express_secrets", rendered.file_path, rendered.anchor)
    literals = re.findall(r"""["']([^"']{12,})["']""", before)
    assert "process.env.GITHUB_TOKEN" in rendered.content
    assert literals
    payload = json.dumps(rendered.to_dict())
    assert all(value not in payload for value in literals)


def test_the_two_stage_dockerfiles_replace_the_file():
    """Each is a whole Dockerfile, so appending it would leave two images in one."""
    for rule_id in ("BLD-006", "BLD-012"):
        assert SHAPES[rule_id].action is Action.CREATE_FILE, rule_id
        assert SHAPES[rule_id].path == "Dockerfile", rule_id


def test_an_env_template_is_created_when_the_project_has_none(tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(SAMPLES / "node_express_secure", root)
    (root / ".env.example").unlink(missing_ok=True)

    created = render(load(root), issue("ENV-001"))

    assert created.action is Action.CREATE_FILE
    assert created.file_path == ".env.example"
    assert created.anchor == ""

    (root / ".env.example").write_text("OTHER=\n", encoding="utf-8")
    added = render(load(root), issue("ENV-001"))

    assert added.action is Action.INSERT_AFTER
    assert added.anchor == "file:end"


def test_a_literal_that_is_not_on_its_line_is_refused():
    got = moved(src_for("node_express_api"), "src/server.js", 1, "not-in-this-file",
                lambda quote: "x")

    assert not got.ok
    assert "unclear" in got.reason
    assert got.values == {}


# --------------------------------------------------------------------------
# the install command follows the lockfile (module 7.3)
# --------------------------------------------------------------------------

# Each place a contract writes an install command, on a sample where it extracts.
INSTALL_SITES = [
    ("node_express_api", "BLD-001"),    # Node Dockerfile
    ("node_express_api", "BLD-003"),    # Node CI workflow
    ("node_express_ready", "BLD-006"),  # Node multi-stage Dockerfile
    ("react_vite_app", "BLD-007"),      # React Dockerfile
    ("react_vite_app", "BLD-010"),      # React CI workflow
    ("react_vite_ready", "BLD-012"),    # React build and serve stages
]


@pytest.mark.parametrize("sample, rule_id", INSTALL_SITES)
def test_a_project_with_no_lockfile_installs_with_npm_install(sample: str, rule_id: str):
    """npm ci refuses without a package-lock.json, found by module 7.3's run."""
    src = src_for(sample)
    assert not src.locks

    got = extract(src, issue(rule_id))
    written = json.dumps(got.values)

    assert got.ok, got.reason
    assert "npm install" in written
    assert "npm ci" not in written


@pytest.mark.parametrize("sample, rule_id", INSTALL_SITES)
def test_a_package_lock_keeps_the_clean_install(sample: str, rule_id: str, tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(SAMPLES / sample, root)
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")

    got = extract(load(root), issue(rule_id))
    written = json.dumps(got.values)

    assert got.ok, got.reason
    assert "npm ci" in written
    assert "npm install" not in written


def test_the_react_images_can_start_as_their_unprivileged_user():
    """Found by module 7.3: nginx could not start as nginx, its cache owned by root."""
    from prodpilot.extraction import DOCKERFILE_REACT, STAGES_REACT

    for template in (DOCKERFILE_REACT, STAGES_REACT):
        lines = template.splitlines()
        user = lines.index("USER nginx")
        assert "chown -R nginx:nginx /var/cache/nginx" in lines[user - 1]
