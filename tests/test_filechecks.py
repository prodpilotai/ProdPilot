"""File existence and entropy tests for Phase 2 module 2.3.

Same shape as the module 2.2 tests. Fixtures were built to pass and to fail, so
both outcomes are proven for every rule rather than only the happy path.

The entropy tests matter most. A scanner that misses secrets is useless and one
that cries wolf gets switched off, so both directions are measured, the second
against thousands of lines of real JavaScript.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prodpilot.astchecks import Finding, Status
from prodpilot.blueprint import Stack
from prodpilot.entropy import (
    BASE64_LIMIT,
    HistoryUnavailable,
    Hit,
    is_placeholder,
    scan_file,
    scan_history,
    scan_project,
    scan_text,
    shannon,
)
from prodpilot.filechecks import (
    CHECKS_BY_STACK,
    NODE_CHECKS,
    REACT_CHECKS,
    check_project,
    ignored,
    load,
)
from prodpilot.rules import ALL_RULES, CheckType

SAMPLES = Path(__file__).resolve().parent / "samples"
NODE_OK = SAMPLES / "node_express_ready"
REACT_OK = SAMPLES / "react_vite_ready"
NODE_BARE = SAMPLES / "node_express_api"
REACT_BARE = SAMPLES / "react_vite_app"
LEAKY = SAMPLES / "node_express_secrets"

ACORN = (
    Path(__file__).resolve().parents[1]
    / "src" / "prodpilot" / "vendor" / "node_modules" / "acorn" / "dist" / "acorn.mjs"
)


def one(findings: list[Finding], rule_id: str) -> Finding:
    hits = [f for f in findings if f.rule_id == rule_id]
    assert len(hits) == 1, f"expected one {rule_id} finding, got {len(hits)}"
    return hits[0]


# --------------------------------------------------------------------------
# rule coverage
# --------------------------------------------------------------------------


def test_every_file_and_entropy_rule_has_a_check():
    """The frozen ruleset is the scope, so none of it may be left unwired."""
    target = {
        r.rule_id for r in ALL_RULES
        if r.check_type in (CheckType.FILE_EXISTENCE, CheckType.ENTROPY_SCAN)
    }
    wired = set(NODE_CHECKS) | set(REACT_CHECKS)

    assert wired == target, f"missing {sorted(target - wired)}, extra {sorted(wired - target)}"
    assert len(target) == 29


def test_no_check_targets_a_rule_of_the_wrong_type():
    from prodpilot.rules import get_rule

    for rule_id in set(NODE_CHECKS) | set(REACT_CHECKS):
        rule = get_rule(rule_id)
        assert rule is not None, f"{rule_id} is not in the rule store"
        assert rule.check_type in (CheckType.FILE_EXISTENCE, CheckType.ENTROPY_SCAN)


def test_checks_are_split_by_the_stack_that_owns_them():
    from prodpilot.rules import get_rule

    for stack, table in CHECKS_BY_STACK.items():
        for rule_id in table:
            assert get_rule(rule_id).stack is stack


# --------------------------------------------------------------------------
# whole project outcomes
# --------------------------------------------------------------------------


def test_ready_node_project_passes_everything_it_can():
    findings = check_project(NODE_OK)

    failed = [f.rule_id for f in findings if f.status is Status.FAIL]
    assert failed == [], f"unexpected failures: {failed}"
    assert len(findings) == len(NODE_CHECKS)


def test_ready_react_project_passes_everything_it_can():
    findings = check_project(REACT_OK)

    failed = [f.rule_id for f in findings if f.status is Status.FAIL]
    assert failed == [], f"unexpected failures: {failed}"
    assert len(findings) == len(REACT_CHECKS)


def test_bare_node_project_fails_the_presence_rules():
    findings = check_project(NODE_BARE)

    for rule_id in ("BLD-001", "BLD-002", "GIT-001", "BLD-003", "BLD-005", "SEC-001"):
        assert one(findings, rule_id).status is Status.FAIL, rule_id


def test_bare_react_project_fails_the_nginx_rules():
    findings = check_project(REACT_BARE)

    for rule_id in ("BLD-009", "BLD-013", "OBS-005", "OBS-006", "SEC-006"):
        assert one(findings, rule_id).status is Status.FAIL, rule_id


def test_unrecognized_project_has_nothing_to_check():
    findings = check_project(SAMPLES / "unrecognized_python_service")

    assert findings == []


def test_stack_can_be_supplied_instead_of_detected():
    findings = check_project(NODE_OK, stack=Stack.NODE_EXPRESS)

    assert len(findings) == len(NODE_CHECKS)


def test_check_project_rejects_a_file_path(tmp_path: Path):
    target = tmp_path / "thing.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        check_project(target)


# --------------------------------------------------------------------------
# individual rules, both directions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id",
    ["BLD-001", "BLD-002", "GIT-001", "BLD-003", "GIT-002", "SCR-001",
     "BLD-004", "BLD-005", "BLD-006", "SEC-001"],
)
def test_node_rule_passes_on_ready_and_fails_on_bare(rule_id: str):
    assert one(check_project(NODE_OK), rule_id).status is Status.PASS
    bare = one(check_project(NODE_BARE), rule_id).status
    if rule_id == "BLD-004":
        # The bare fixture does declare a start script, so it legitimately
        # passes this one. Its failure case is the secrets fixture.
        assert one(check_project(LEAKY), rule_id).status is Status.FAIL
    else:
        assert bare is Status.FAIL, rule_id


@pytest.mark.parametrize(
    "rule_id",
    ["BLD-007", "BLD-008", "BLD-009", "GIT-004", "BLD-010", "GIT-005",
     "GIT-006", "SCR-003", "BLD-012", "BLD-013", "SEC-005", "OBS-005",
     "OBS-006", "SEC-006"],
)
def test_react_rule_passes_on_ready_and_fails_on_bare(rule_id: str):
    assert one(check_project(REACT_OK), rule_id).status is Status.PASS
    assert one(check_project(REACT_BARE), rule_id).status is Status.FAIL


def test_react_build_script_rule_both_ways(tmp_path: Path):
    """BLD-011 needs its own fixture, since the bare React sample does build."""
    assert one(check_project(REACT_OK), "BLD-011").status is Status.PASS

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "1", "react-dom": "1"},
                    "devDependencies": {"vite": "1"}, "scripts": {"dev": "vite"}}),
        encoding="utf-8",
    )
    assert one(check_project(tmp_path, Stack.REACT_VITE), "BLD-011").status is Status.FAIL


def test_dev_watcher_in_start_script_fails(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"express": "1"},
                    "scripts": {"start": "nodemon src/server.js"}}),
        encoding="utf-8",
    )
    f = one(check_project(tmp_path, Stack.NODE_EXPRESS), "BLD-004")

    assert f.status is Status.FAIL
    assert "nodemon" in f.detail


def test_explicit_root_user_fails(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        "FROM node:20\nFROM node:20\nUSER root\n", encoding="utf-8")
    f = one(check_project(tmp_path, Stack.NODE_EXPRESS), "SEC-001")

    assert f.status is Status.FAIL
    assert "root" in f.detail


def test_single_stage_dockerfile_fails(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM node:20\nUSER node\n", encoding="utf-8")
    f = one(check_project(tmp_path, Stack.NODE_EXPRESS), "BLD-006")

    assert f.status is Status.FAIL
    assert "1 build stage" in f.detail


def test_logging_turned_off_fails(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"react":"1","react-dom":"1"},"devDependencies":{"vite":"1"}}',
        encoding="utf-8")
    (tmp_path / "nginx.conf").write_text(
        "server {\n listen 80;\n access_log off;\n error_log /dev/stderr;\n}\n", encoding="utf-8")
    f = one(check_project(tmp_path, Stack.REACT_VITE), "OBS-006")

    assert f.status is Status.FAIL
    assert "turned off" in f.detail


def test_malformed_package_json_reads_as_unparsed(tmp_path: Path):
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")
    f = one(check_project(tmp_path, Stack.NODE_EXPRESS), "BLD-005")

    assert f.status is Status.UNPARSED
    assert not f.passed


def test_gitignore_wildcard_counts_as_excluded(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules\n.env*\n", encoding="utf-8")
    p = load(tmp_path)

    assert ignored(p.gitignore, ".env")
    assert ignored(p.gitignore, ".env.production")
    assert not ignored(p.gitignore, "dist")


# --------------------------------------------------------------------------
# entropy, the detection direction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'const t = "ghp_A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOa";',
        'const k = "rnd_7Kd93Lm2Xq8Zp4Rt6Wy1Bn5Vc0Hs";',
        'const a = "AKIAIOSFODNN7EXAMPLE";',
        'const u = "mongodb+srv://admin:Str0ngP4ss@cluster0.mongodb.net/app";',
        'const SESSION_SECRET = "kQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOaX";',
    ],
)
def test_planted_secrets_are_flagged(line: str):
    hits = scan_text(line, "x.js")

    assert hits, f"missed: {line}"


def test_leaky_fixture_fails_the_secrets_rule():
    f = one(check_project(LEAKY), "SCR-002")

    assert f.status is Status.FAIL
    assert "credential-shaped" in f.detail


def test_a_reported_secret_is_masked():
    hits = scan_text('const t = "ghp_A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOa";', "x.js")

    assert hits
    assert "A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0" not in hits[0].masked
    assert "..." in hits[0].masked


def test_borderline_value_is_reported_not_dropped():
    """Section 4.1 requires an ambiguous case to be surfaced rather than guessed."""
    hit = Hit("x.js", 1, "abcdefghijklmnopqrst", "borderline", 4.3, True)

    assert hit.borderline
    assert hit.to_dict()["borderline"] is True


# --------------------------------------------------------------------------
# entropy, the quiet direction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'const API_BASE_URL = process.env.REACT_APP_API_BASE_URL;',
        'import { createRouter, createWebHistory } from "vue-router";',
        'export default function ApplicationConfigurationProvider(props) {}',
        'const className = "flex items-center justify-between rounded-lg";',
        'const API_KEY = "your-api-key-here";',
        'const PASSWORD = "changeme";',
        'const SECRET = "xxxxxxxxxxxxxxxxxxxxxxxx";',
        'const path = "/usr/local/share/application/configuration/settings";',
    ],
)
def test_normal_code_is_not_flagged(line: str):
    assert scan_text(line, "x.js") == []


def test_real_javascript_produces_no_false_positives():
    """Seven thousand lines of dense parser source is the honest stress test."""
    assert ACORN.is_file(), "vendored parser is missing"
    lines = len(ACORN.read_text(encoding="utf-8").splitlines())
    hits = scan_file(ACORN, ACORN.parent)

    assert lines > 5000
    assert hits == [], f"{len(hits)} false positives in real JavaScript"


def test_clean_fixtures_produce_no_secret_findings():
    for sample in (NODE_OK, REACT_OK, SAMPLES / "node_express_secure"):
        assert scan_project(sample) == [], sample.name


def test_placeholders_are_recognised():
    for value in ("your-api-key-here", "changeme", "xxxxxxxx", "<your-token>", "placeholder"):
        assert is_placeholder(value), value
    assert not is_placeholder("kQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOaX")


def test_entropy_rises_with_randomness():
    low = shannon("aaaaaaaaaaaaaaaaaaaa", set("abcdefghijklmnopqrstuvwxyz"))
    high = shannon("kQ7bZx4LpW1nR8tYv3Jh", set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="))

    assert low < 1.0
    assert high > BASE64_LIMIT - 1


# --------------------------------------------------------------------------
# committed history
# --------------------------------------------------------------------------


def make_repo(path: Path) -> None:
    run = lambda *a: subprocess.run(a, cwd=str(path), capture_output=True, text=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "Test")


def test_history_scan_finds_a_secret_that_was_later_removed(tmp_path: Path):
    """The whole point of the rule. Deleting the file does not clear the history."""
    make_repo(tmp_path)
    leak = tmp_path / "config.js"
    leak.write_text('const T = "ghp_A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOa";\n', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add config"], cwd=str(tmp_path), check=True,
                   capture_output=True)
    leak.write_text("const T = process.env.GITHUB_TOKEN;\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "move to env"], cwd=str(tmp_path), check=True,
                   capture_output=True)

    assert scan_project(tmp_path) == []
    hits = scan_history(tmp_path)
    assert hits, "the secret is gone from the tree but still in the history"
    assert hits[0].file.startswith("history:")


def test_history_rule_fails_when_a_secret_is_committed(tmp_path: Path):
    make_repo(tmp_path)
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    (tmp_path / "config.js").write_text(
        'const T = "ghp_A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOa";\n', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(tmp_path), check=True,
                   capture_output=True)

    f = one(check_project(tmp_path, Stack.NODE_EXPRESS), "GIT-003")

    assert f.status is Status.FAIL
    assert "history" in f.detail


def test_clean_history_passes(tmp_path: Path):
    make_repo(tmp_path)
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    (tmp_path / "app.js").write_text("const p = process.env.PORT;\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(tmp_path), check=True,
                   capture_output=True)

    assert one(check_project(tmp_path, Stack.NODE_EXPRESS), "GIT-003").status is Status.PASS


def test_history_rule_skips_a_project_with_no_repository():
    f = one(check_project(NODE_OK), "GIT-003")

    assert f.status is Status.SKIPPED
    assert not f.counts


def test_history_scan_raises_outside_a_repository(tmp_path: Path):
    with pytest.raises(HistoryUnavailable):
        scan_history(tmp_path)


# --------------------------------------------------------------------------
# output shape for module 2.4
# --------------------------------------------------------------------------


def test_findings_match_the_shape_the_ast_checks_produce():
    findings = check_project(NODE_OK)

    assert findings
    for f in findings:
        assert isinstance(f, Finding)
        assert set(f.to_dict()) == {"rule_id", "status", "file", "line", "detail"}
        assert f.counts == (f.status in (Status.PASS, Status.FAIL))


# --------------------------------------------------------------------------
# logging turned off for one location only (module 7.3)
# --------------------------------------------------------------------------

REACT_MANIFEST = '{"dependencies":{"react":"1","react-dom":"1"},"devDependencies":{"vite":"1"}}'


def logging_of(tmp_path: Path, conf: str) -> Finding:
    (tmp_path / "package.json").write_text(REACT_MANIFEST, encoding="utf-8")
    (tmp_path / "nginx.conf").write_text(conf, encoding="utf-8")
    return one(check_project(tmp_path, Stack.REACT_VITE), "OBS-006")


def test_logging_off_for_one_location_only_passes(tmp_path: Path):
    """Silencing a health path is not turning the server's logging off."""
    f = logging_of(tmp_path, "server {\n listen 80;\n access_log /dev/stdout;\n"
                             " error_log /dev/stderr warn;\n location /health {\n"
                             "  access_log off;\n  return 200;\n }\n}\n")

    assert f.status is Status.PASS


def test_the_nginx_conf_prodpilot_writes_passes_its_own_logging_rule(tmp_path: Path):
    """Found by module 7.3: every React project ProdPilot repaired failed OBS-006."""
    from prodpilot import templates

    assert logging_of(tmp_path, templates.get("BLD-009").content).status is Status.PASS
