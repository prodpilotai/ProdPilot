"""AST check tests for Phase 2 module 2.2.

Two layers. The parser tests confirm the vendored acorn integration handles the
syntax real projects contain and fails closed on the syntax it cannot read. The
check tests run every check against committed fixtures that were built to pass
and to fail, so both outcomes are proven rather than only the happy path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prodpilot.astchecks import (
    CHECKS,
    PROJECT_CHECKS,
    Finding,
    Status,
    check_file,
    check_project,
    is_app,
    js_files,
)
from prodpilot.jsparse import (
    ParseError,
    Tree,
    calls,
    find,
    imports,
    line_of,
    name_of,
    parse,
    parse_file,
    walk,
)
from prodpilot.rules import CheckType, get_rule

SAMPLES = Path(__file__).resolve().parent / "samples"
SECURE = SAMPLES / "node_express_secure"
INSECURE = SAMPLES / "node_express_insecure"
WILDCARD = SAMPLES / "node_express_wildcard"


def only(findings: list[Finding], rule_id: str, name: str) -> Finding:
    """The finding for one rule against one file."""
    hits = [f for f in findings if f.rule_id == rule_id and f.file.endswith(name)]
    assert len(hits) == 1, f"expected one {rule_id} finding for {name}, got {len(hits)}"
    return hits[0]


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        'const express = require("express");',
        'import express from "express";',
        "async function m() { await go(); }",
        "const v = a?.b?.c;",
        "const v = a ?? b;",
        "class A { x = 1; }",
        "const o = { ...a, b };",
        "const el = <div className='x'>hi</div>;",
        "const el = <><A /><B /></>;",
    ],
)
def test_parser_handles_syntax_real_projects_use(src: str):
    """The esprima Python port fails several of these, which is why acorn is used."""
    tree = parse(src)

    assert tree.ok, f"{src!r} did not parse: {tree.error}"
    assert tree.ast is not None


def test_broken_source_is_reported_not_raised():
    """Unparseable source is a fact about the project, not a toolchain failure."""
    tree = parse("const a = {{{ ;", path="broken.js")

    assert not tree.ok
    assert tree.line == 1
    assert "Unexpected token" in tree.error
    assert tree.body() == []


def test_missing_file_reads_as_unparseable():
    tree = parse_file(SAMPLES / "does-not-exist.js")

    assert not tree.ok
    assert "cannot read file" in tree.error


def test_parse_error_is_raised_when_the_parser_cannot_run(monkeypatch):
    """A broken toolchain must raise, not silently report every file as failing."""
    import prodpilot.jsparse as jsparse

    monkeypatch.setattr(jsparse.shutil, "which", lambda _: None)
    with pytest.raises(ParseError):
        parse("const a = 1;")


def test_tree_helpers_read_the_shape_of_a_server():
    src = (
        'const express = require("express");\n'
        'const helmet = require("helmet");\n'
        "const app = express();\n"
        "app.use(helmet());\n"
        'app.get("/health", (req, res) => res.json({ ok: true }));\n'
    )
    tree = parse(src)

    assert imports(tree) == {"express": 1, "helmet": 2}
    assert [line_of(c) for c in calls(tree, "app.use")] == [4]
    assert [line_of(c) for c in calls(tree, "app.get")] == [5]
    assert len(find(tree.ast, "CallExpression")) > 0
    assert sum(1 for _ in walk(tree.ast)) > 10


def test_name_of_reads_dotted_members():
    tree = parse("app.use(express.json());")
    call = find(tree.ast, "CallExpression")[0]

    assert name_of(call.get("callee")) == "app.use"


# --------------------------------------------------------------------------
# rule mapping
# --------------------------------------------------------------------------


def test_every_check_maps_to_an_ast_rule_in_the_frozen_store():
    """No check may invent a rule, and none may target a non-AST rule."""
    for rule_id in CHECKS:
        rule = get_rule(rule_id)
        assert rule is not None, f"{rule_id} is not in the rule store"
        assert rule.check_type is CheckType.AST, f"{rule_id} is not an AST rule"


def test_findings_carry_the_fields_module_two_four_needs():
    findings = check_project(SECURE)

    assert findings
    for f in findings:
        assert f.rule_id in CHECKS or f.rule_id in PROJECT_CHECKS
        assert isinstance(f.status, Status)
        assert f.file
        assert isinstance(f.to_dict(), dict)
        assert set(f.to_dict()) == {"rule_id", "status", "file", "line", "detail"}


def test_only_pass_and_fail_count_towards_a_score():
    assert Finding("SEC-002", Status.PASS, "a.js").counts
    assert Finding("SEC-002", Status.FAIL, "a.js").counts
    assert not Finding("SEC-002", Status.SKIPPED, "a.js").counts
    assert not Finding("SEC-002", Status.UNPARSED, "a.js").counts


def test_an_unparsed_file_never_reads_as_passing():
    """Fail closed. A file that cannot be analysed has not satisfied anything."""
    assert not Finding("SEC-002", Status.UNPARSED, "a.js").passed


# --------------------------------------------------------------------------
# SEC-002, helmet placement
# --------------------------------------------------------------------------


def test_helmet_before_routes_passes():
    f = only(check_project(SECURE), "SEC-002", "server.js")

    assert f.status is Status.PASS


def test_helmet_after_routes_fails_with_both_lines():
    f = only(check_project(INSECURE), "SEC-002", "server.js")

    assert f.status is Status.FAIL
    assert f.line == 21
    assert "after the first route" in f.detail


def test_missing_helmet_fails():
    f = only(check_project(WILDCARD), "SEC-002", "server.js")

    assert f.status is Status.FAIL
    assert "never registered" in f.detail


# --------------------------------------------------------------------------
# SEC-003, CORS configuration
# --------------------------------------------------------------------------


def test_cors_origin_from_env_passes():
    f = only(check_project(SECURE), "SEC-003", "server.js")

    assert f.status is Status.PASS


def test_hardcoded_cors_origin_fails():
    f = only(check_project(INSECURE), "SEC-003", "server.js")

    assert f.status is Status.FAIL
    assert "hardcoded" in f.detail


def test_wildcard_cors_origin_fails():
    f = only(check_project(WILDCARD), "SEC-003", "server.js")

    assert f.status is Status.FAIL
    assert "wildcard" in f.detail


def test_missing_cors_fails(tmp_path: Path):
    src = tmp_path / "server.js"
    src.write_text(
        'const express = require("express");\n'
        "const app = express();\n"
        'app.get("/x", (req, res) => res.end());\n',
        encoding="utf-8",
    )
    f = only(check_file(src, tmp_path), "SEC-003", "server.js")

    assert f.status is Status.FAIL
    assert "never configured" in f.detail


# --------------------------------------------------------------------------
# API-003, error handler placement, the other half of middleware ordering
# --------------------------------------------------------------------------


def test_error_handler_after_routes_passes():
    f = only(check_project(SECURE), "API-003", "server.js")

    assert f.status is Status.PASS


def test_error_handler_before_last_route_fails():
    """A four argument handler registered too early never runs."""
    f = only(check_project(INSECURE), "API-003", "server.js")

    assert f.status is Status.FAIL
    assert f.line == 13
    assert "never runs" in f.detail


def test_missing_error_handler_fails():
    f = only(check_project(WILDCARD), "API-003", "server.js")

    assert f.status is Status.FAIL
    assert "no central error handler" in f.detail


# --------------------------------------------------------------------------
# STR-001 and STR-002, the structural rules
# --------------------------------------------------------------------------


def test_controller_without_db_calls_passes():
    f = only(check_project(SECURE), "STR-001", "userController.js")

    assert f.status is Status.PASS


def test_controller_with_db_calls_fails():
    f = only(check_project(INSECURE), "STR-001", "invoiceController.js")

    assert f.status is Status.FAIL
    assert f.line == 4
    assert "belong in a service" in f.detail


def test_delegating_route_handler_passes():
    f = only(check_project(SECURE), "STR-002", "userRoutes.js")

    assert f.status is Status.PASS


def test_route_handler_querying_the_database_fails():
    f = only(check_project(INSECURE), "STR-002", "invoiceRoutes.js")

    assert f.status is Status.FAIL
    assert "instead of calling a service" in f.detail


def test_fat_route_handler_fails(tmp_path: Path):
    """Inline logic is a violation even without a database call."""
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    body = "\n".join(f"  const v{i} = {i} * 2;" for i in range(10))
    (routes_dir / "thing.js").write_text(
        'const express = require("express");\n'
        "const router = express.Router();\n"
        'router.get("/", (req, res) => {\n' + body + "\n  res.end();\n});\n",
        encoding="utf-8",
    )
    f = only(check_file(routes_dir / "thing.js", tmp_path), "STR-002", "thing.js")

    assert f.status is Status.FAIL
    assert "inline" in f.detail


def test_structure_rules_skip_files_they_do_not_govern():
    findings = check_project(SECURE)

    assert only(findings, "STR-001", "server.js").status is Status.SKIPPED
    assert only(findings, "STR-002", "userController.js").status is Status.SKIPPED


# --------------------------------------------------------------------------
# scoping
# --------------------------------------------------------------------------


def test_app_level_checks_skip_router_files():
    """Every router file would otherwise report three violations it cannot fix."""
    findings = check_project(SECURE)

    for rule_id in ("SEC-002", "SEC-003", "API-003"):
        assert only(findings, rule_id, "userRoutes.js").status is Status.SKIPPED


def test_is_app_separates_the_app_from_a_router():
    app = parse('const express = require("express");\nconst app = express();\n')
    router = parse('const express = require("express");\nconst r = express.Router();\n')

    assert is_app(app)
    assert not is_app(router)


def test_react_project_is_skipped_by_the_express_checks():
    """A React project does not match any Express specific rule."""
    findings = check_project(SAMPLES / "react_vite_app")
    express_only = {"SEC-002", "SEC-003", "SEC-004", "API-001", "API-003",
                    "ENV-002", "OBS-001", "OBS-002", "OBS-003", "OBS-004"}

    assert findings
    for f in findings:
        if f.rule_id in express_only:
            assert f.status is Status.SKIPPED, f.rule_id


def test_jsx_fixture_parses_rather_than_erroring():
    tree = parse_file(SAMPLES / "react_vite_app" / "src" / "main.jsx")

    assert tree.ok, tree.error


def test_build_output_is_not_scanned(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("const a = 1;", encoding="utf-8")
    for skip in ("node_modules", "dist"):
        d = tmp_path / skip
        d.mkdir()
        (d / "b.js").write_text("const b = 1;", encoding="utf-8")

    found = [p.name for p in js_files(tmp_path)]

    assert found == ["a.js"]


def test_check_project_rejects_a_path_that_is_not_a_directory(tmp_path: Path):
    target = tmp_path / "file.js"
    target.write_text("const a = 1;", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        check_project(target)


def test_unparseable_file_is_reported_as_unparsed(tmp_path: Path):
    bad = tmp_path / "server.js"
    bad.write_text("const a = {{{ ;", encoding="utf-8")

    findings = check_file(bad, tmp_path)

    assert findings
    statuses = {f.status for f in findings}
    assert Status.UNPARSED in statuses
    assert Status.PASS not in statuses


# --------------------------------------------------------------------------
# the rules completed in the second pass over module 2.2
# --------------------------------------------------------------------------

HARD_NODE = SAMPLES / "node_express_hardened"
HARD_REACT = SAMPLES / "react_vite_hardened"


def rule(findings: list[Finding], rule_id: str) -> Finding:
    """The single non-skipped finding for a rule across a project."""
    live = [f for f in findings if f.rule_id == rule_id and f.status is not Status.SKIPPED]
    assert len(live) == 1, f"expected one live {rule_id} finding, got {len(live)}"
    return live[0]


def node_project(tmp_path: Path, server: str, extra: dict[str, str] | None = None) -> Path:
    """Write a minimal Express project for a single rule's failure case."""
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    (tmp_path / "server.js").write_text(server, encoding="utf-8")
    for name, body in (extra or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


def react_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a minimal React project for a single rule's failure case."""
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"react":"1","react-dom":"1"},"devDependencies":{"vite":"1"}}',
        encoding="utf-8",
    )
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


def test_every_ast_rule_in_the_store_now_has_a_check():
    """Closes the gap the module 2.2 report left open at 5 of 21."""
    from prodpilot.rules import ALL_RULES

    target = {r.rule_id for r in ALL_RULES if r.check_type is CheckType.AST}
    wired = set(CHECKS) | set(PROJECT_CHECKS)

    assert wired == target, f"missing {sorted(target - wired)}"
    assert len(target) == 21


def test_project_checks_carry_the_cross_file_rules():
    """A cross-file rule cannot be decided from one file, so it runs once."""
    for rule_id in PROJECT_CHECKS:
        assert get_rule(rule_id).scope.value == "cross_file", rule_id


def test_hardened_node_project_passes_every_applicable_rule():
    findings = check_project(HARD_NODE)

    failed = sorted({f.rule_id for f in findings if f.status is Status.FAIL})
    assert failed == [], f"unexpected failures: {failed}"


def test_hardened_react_project_passes_every_applicable_rule():
    findings = check_project(HARD_REACT)

    failed = sorted({f.rule_id for f in findings if f.status is Status.FAIL})
    assert failed == [], f"unexpected failures: {failed}"


def test_csp_and_https_redirect_pass_together():
    assert rule(check_project(HARD_NODE), "SEC-004").status is Status.PASS


def test_missing_https_redirect_fails():
    f = rule(check_project(SECURE), "SEC-004")

    assert f.status is Status.FAIL
    assert "HTTPS redirect" in f.detail


def test_port_from_env_passes():
    assert rule(check_project(HARD_NODE), "ENV-002").status is Status.PASS


def test_hardcoded_port_fails():
    f = rule(check_project(INSECURE), "ENV-002")

    assert f.status is Status.FAIL
    assert "3000" in f.detail


def test_pooled_connection_from_env_passes():
    findings = check_project(HARD_NODE)

    assert rule(findings, "CON-001").status is Status.PASS
    assert rule(findings, "CON-002").status is Status.PASS


def test_inline_credentials_fail(tmp_path: Path):
    node_project(
        tmp_path,
        'const express = require("express");\nconst app = express();\n',
        {"db.js": 'const { Pool } = require("pg");\n'
                  'const pool = new Pool({ connectionString: '
                  '"postgres://admin:hunter2@db.example.com:5432/app" });\n'},
    )
    f = rule(check_project(tmp_path), "CON-001")

    assert f.status is Status.FAIL
    assert "inline credentials" in f.detail


def test_unpooled_client_fails(tmp_path: Path):
    node_project(
        tmp_path,
        'const express = require("express");\nconst app = express();\n',
        {"db.js": 'const { Client } = require("pg");\n'
                  "const c = new Client({ connectionString: process.env.DATABASE_URL });\n"},
    )
    f = rule(check_project(tmp_path), "CON-002")

    assert f.status is Status.FAIL
    assert "no pool" in f.detail


def test_rate_limiter_passes():
    assert rule(check_project(HARD_NODE), "API-001").status is Status.PASS


def test_missing_rate_limiter_fails():
    assert rule(check_project(SECURE), "API-001").status is Status.FAIL


def test_versioned_mount_passes():
    f = rule(check_project(HARD_NODE), "API-002")

    assert f.status is Status.PASS
    assert "/api/v1" in f.detail


def test_unversioned_mount_fails():
    f = rule(check_project(WILDCARD), "API-002")

    assert f.status is Status.FAIL
    assert "no versioned prefix" in f.detail


def test_observability_rules_pass_on_the_hardened_project():
    findings = check_project(HARD_NODE)

    for rule_id in ("OBS-001", "OBS-002", "OBS-003", "OBS-004"):
        assert rule(findings, rule_id).status is Status.PASS, rule_id


def test_observability_rules_fail_on_a_bare_server():
    findings = check_project(SECURE)

    for rule_id in ("OBS-002", "OBS-003", "OBS-004"):
        assert rule(findings, rule_id).status is Status.FAIL, rule_id


def test_missing_health_endpoint_fails():
    f = rule(check_project(WILDCARD), "OBS-001")

    assert f.status is Status.FAIL
    assert "health" in f.detail


def test_env_example_covering_every_key_passes():
    assert rule(check_project(HARD_NODE), "ENV-001").status is Status.PASS
    assert rule(check_project(HARD_REACT), "ENV-003").status is Status.PASS


def test_missing_env_example_fails():
    f = rule(check_project(SECURE), "ENV-001")

    assert f.status is Status.FAIL
    assert "no .env.example" in f.detail


def test_env_example_missing_a_key_fails(tmp_path: Path):
    node_project(
        tmp_path,
        'const express = require("express");\nconst app = express();\n'
        "const a = process.env.PORT;\nconst b = process.env.SESSION_SECRET;\n",
    )
    (tmp_path / ".env.example").write_text("PORT=3000\n", encoding="utf-8")
    f = rule(check_project(tmp_path), "ENV-001")

    assert f.status is Status.FAIL
    assert "SESSION_SECRET" in f.detail


def test_vite_coverage_ignores_keys_the_client_cannot_read(tmp_path: Path):
    """Vite only exposes VITE_ names, so others are not required in the template."""
    react_project(tmp_path, {
        "app.jsx": "const a = import.meta.env.VITE_API_URL;\n"
                   "const b = import.meta.env.MODE;\n",
        ".env.example": "VITE_API_URL=https://x.example.com\n",
    })
    assert rule(check_project(tmp_path), "ENV-003").status is Status.PASS


def test_api_url_from_import_meta_env_passes():
    assert rule(check_project(HARD_REACT), "ENV-004").status is Status.PASS


def test_literal_api_url_fails(tmp_path: Path):
    react_project(tmp_path, {
        "api.js": 'export const get = () => fetch("https://api.example.com/orders");\n',
    })
    findings = check_project(tmp_path)

    assert rule(findings, "ENV-004").status is Status.FAIL
    assert rule(findings, "ENV-005").status is Status.FAIL


def test_localhost_is_not_treated_as_a_backend_host(tmp_path: Path):
    """A development URL is not a deployment problem, so it must not be flagged."""
    node_project(
        tmp_path,
        'const express = require("express");\nconst app = express();\n'
        'const dev = "http://localhost:3000/api";\n',
    )
    assert rule(check_project(tmp_path), "ENV-005").status is Status.PASS


def test_error_boundary_wrapping_the_root_passes():
    f = rule(check_project(HARD_REACT), "STR-003")

    assert f.status is Status.PASS
    assert "ErrorBoundary" in f.detail


def test_missing_error_boundary_fails():
    f = rule(check_project(SAMPLES / "react_vite_app"), "STR-003")

    assert f.status is Status.FAIL
    assert "no error boundary" in f.detail


def test_catch_all_route_passes():
    assert rule(check_project(HARD_REACT), "STR-004").status is Status.PASS


def test_router_without_a_catch_all_fails(tmp_path: Path):
    react_project(tmp_path, {
        "main.jsx": 'import { Routes, Route } from "react-router-dom";\n'
                    "export default () => (\n  <Routes>\n"
                    '    <Route path="/" element={<Home />} />\n  </Routes>\n);\n',
    })
    f = rule(check_project(tmp_path), "STR-004")

    assert f.status is Status.FAIL
    assert "no catch all" in f.detail


def test_express_file_is_not_mistaken_for_a_client_router(tmp_path: Path):
    """An object carrying a path key is not a route table."""
    node_project(
        tmp_path,
        'const express = require("express");\nconst app = express();\n'
        'app.use((err, req, res, next) => { log("failed", { path: req.path }); });\n',
    )
    findings = [f for f in check_project(tmp_path) if f.rule_id == "STR-004"]

    assert all(f.status is Status.SKIPPED for f in findings)
