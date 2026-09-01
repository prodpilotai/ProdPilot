"""DYNAMIC-DELEGATED constraint tests for Phase 3 module 3.4.

The field that carries the weight is requirement. Section 5.2 says it is a
checkable condition on purpose, because it is what independent re-verification
evaluates afterward, so it is not enough to assert the text exists.

Three things are proven about it. The verifier each contract names resolves to a
real function. The text contains the same concrete tokens that function keys on.
And, for every rule where it is practical, a project built to satisfy the
requirement as written is then accepted by that very checker, which is the only
way to show the words and the check agree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prodpilot import astchecks, entropy
from prodpilot.astchecks import BOUNDARY_HOOKS, DB_METHODS, check_project
from prodpilot.audit import RuleResult, run
from prodpilot.findings import Finding, Status
from prodpilot.loop import Loop, Outcome, Step
from prodpilot.rules import ALL_RULES, FixType, get_rule
from prodpilot.constraints import (
    ACTION,
    CONSTRAINT,
    CONTRACTS,
    REPO,
    Contract,
    ContractError,
    covers,
    delegated_rules,
    get,
    render,
    resolver,
    target,
    violation,
)

SAMPLES = Path(__file__).resolve().parent / "samples"
DD_IDS = sorted(r.rule_id for r in ALL_RULES if r.fix_type is FixType.DYNAMIC_DELEGATED)

MODULES = {"astchecks": astchecks, "entropy": entropy}

# Words that make a requirement unverifiable. If a checker cannot decide it,
# neither can an agent.
VAGUE = (
    "properly", "correctly", "appropriately", "cleanly", "sensibly", "nicely",
    "good", "better", "best practice", "as needed", "if possible", "consider",
    "try to", "where appropriate", "reasonable", "idiomatic", "tidy",
)


def issue(rule_id: str, file: str = "src/server.js", line: int = 4) -> RuleResult:
    rule = get_rule(rule_id)
    assert rule is not None, rule_id
    hit = Finding(rule_id, Status.FAIL, file, line, f"{rule_id} is not satisfied")
    return RuleResult(rule=rule, status=Status.FAIL, evidence=(hit,))


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


def commit(root: Path, name: str, body: str, message: str) -> None:
    """A one commit repository, so scan_history has real history to read."""
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / name).write_text(body, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def verdict(root: Path, rule_id: str):
    """What the real checker says about a project, ignoring skips."""
    hits = [f for f in check_project(root)
            if f.rule_id == rule_id and f.status is not Status.SKIPPED]
    assert hits, f"{rule_id} did not evaluate against {root}"
    return hits[0]


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def test_every_delegated_rule_has_a_contract():
    target_ids = {r.rule_id for r in delegated_rules()}

    assert set(CONTRACTS) == target_ids, f"missing {sorted(target_ids - set(CONTRACTS))}"
    assert len(target_ids) == 6


def test_no_rule_of_another_fix_type_is_wired_here():
    for rule_id in CONTRACTS:
        assert get_rule(rule_id).fix_type is FixType.DYNAMIC_DELEGATED, rule_id


def test_template_ids_match_the_frozen_store():
    for rule_id, contract in CONTRACTS.items():
        assert contract.template_id == get_rule(rule_id).constraint_template, rule_id


def test_the_two_history_rules_are_included():
    """The module description says structural, the frozen store says otherwise."""
    assert covers("GIT-003")
    assert covers("GIT-007")
    assert get_rule("GIT-003").domain.value == "git_hygiene"


def test_covers_answers_for_both_cases():
    assert covers("STR-001")
    assert not covers("SEC-002")
    assert not covers("NOPE-000")


# --------------------------------------------------------------------------
# the requirement is a checkable condition
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", DD_IDS)
def test_the_named_verifier_is_a_real_function(rule_id: str):
    """A requirement tied to nothing is prose, not a condition."""
    module, name = get(rule_id).verifier.split(".")

    assert callable(getattr(MODULES[module], name, None)), get(rule_id).verifier


@pytest.mark.parametrize("rule_id", DD_IDS)
def test_no_requirement_uses_a_word_a_checker_cannot_decide(rule_id: str):
    text = get(rule_id).requirement.lower()

    for word in VAGUE:
        assert word not in text, f"{rule_id} says {word}"


@pytest.mark.parametrize("rule_id", DD_IDS)
def test_each_requirement_states_a_condition_not_an_instruction(rule_id: str):
    """After the change, X holds. Not: go and do X."""
    text = get(rule_id).requirement.lower()

    assert "after the change" in text
    assert any(w in text for w in ("no ", "every ", "exists", "contains", "finds"))


def test_the_service_layer_requirement_names_what_the_checker_looks_for():
    text = get("STR-001").requirement.lower()

    packed = text.replace(" ", "")
    named = {m for m in DB_METHODS if m.lower() in packed}
    assert len(named) >= 5, f"only named {sorted(named)}"
    assert "services" in text


def test_the_thin_handler_requirement_carries_the_checkers_threshold():
    """check_thin_routes fails above 8 statements, so the number must be stated."""
    assert "8 statements" in get("STR-002").requirement


def test_the_boundary_requirement_names_both_lifecycle_hooks():
    text = get("STR-003").requirement

    for hook in BOUNDARY_HOOKS:
        assert hook in text, hook


def test_the_catch_all_requirement_names_the_accepted_paths():
    text = get("STR-004").requirement

    assert "*" in text and "/*" in text


def test_the_history_requirement_demands_rotation_not_only_rewriting():
    """Rewriting history does not un-leak a credential that is still live."""
    for rule_id in ("GIT-003", "GIT-007"):
        text = get(rule_id).requirement.lower()
        assert "rotat" in text, rule_id
        assert "git log" in text, rule_id


# --------------------------------------------------------------------------
# the requirement, satisfied, is accepted by the checker that will verify it
# --------------------------------------------------------------------------


def test_a_controller_satisfying_str_001_passes_its_checker(tmp_path: Path):
    """The requirement says no driver call remains and the calls live in a service."""
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    for folder in ("controllers", "services"):
        (tmp_path / "src" / folder).mkdir(parents=True)
    (tmp_path / "src" / "services" / "invoiceService.js").write_text(
        'const Invoice = require("../models/Invoice");\n'
        "async function list() {\n  return Invoice.find({ paid: false });\n}\n"
        "module.exports = { list };\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "controllers" / "invoiceController.js").write_text(
        'const invoiceService = require("../services/invoiceService");\n'
        "async function list(req, res, next) {\n"
        "  try {\n    res.json({ invoices: await invoiceService.list() });\n"
        "  } catch (err) {\n    next(err);\n  }\n}\n"
        "module.exports = { list };\n",
        encoding="utf-8",
    )

    assert verdict(tmp_path, "STR-001").status is Status.PASS


def test_a_route_file_satisfying_str_002_passes_its_checker(tmp_path: Path):
    """No database call in a handler, and no handler over the stated threshold."""
    (tmp_path / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
    (tmp_path / "src" / "routes").mkdir(parents=True)
    (tmp_path / "src" / "routes" / "invoiceRoutes.js").write_text(
        'const express = require("express");\n'
        'const controller = require("../controllers/invoiceController");\n'
        "const router = express.Router();\n"
        'router.get("/", controller.list);\n'
        "module.exports = router;\n",
        encoding="utf-8",
    )

    assert verdict(tmp_path, "STR-002").status is Status.PASS


def test_a_root_satisfying_str_003_passes_its_checker(tmp_path: Path):
    """A class with the named hooks, rendered around the application root."""
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"react":"1","react-dom":"1"},"devDependencies":{"vite":"1"}}',
        encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ErrorBoundary.jsx").write_text(
        'import React from "react";\n'
        "export class ErrorBoundary extends React.Component {\n"
        "  static getDerivedStateFromError(err) {\n    return { failed: true };\n  }\n"
        "  componentDidCatch(err, info) {\n    console.error(err, info);\n  }\n"
        "  render() {\n    return this.state && this.state.failed ? <p>Error</p> : this.props.children;\n  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.jsx").write_text(
        'import ReactDOM from "react-dom/client";\n'
        'import { ErrorBoundary } from "./ErrorBoundary";\n'
        "function App() {\n  return <h1>Invoices</h1>;\n}\n"
        'ReactDOM.createRoot(document.getElementById("root")).render(\n'
        "  <ErrorBoundary><App /></ErrorBoundary>,\n);\n",
        encoding="utf-8",
    )

    assert verdict(tmp_path, "STR-003").status is Status.PASS


def test_a_router_satisfying_str_004_passes_its_checker(tmp_path: Path):
    """A Route whose path is one of the two literals the requirement names."""
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"react":"1","react-dom":"1"},"devDependencies":{"vite":"1"}}',
        encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "routes.jsx").write_text(
        'import { Routes, Route } from "react-router-dom";\n'
        "export default function AppRoutes() {\n"
        "  return (\n    <Routes>\n"
        '      <Route path="/" element={<Home />} />\n'
        '      <Route path="*" element={<NotFound />} />\n'
        "    </Routes>\n  );\n}\n",
        encoding="utf-8",
    )

    assert verdict(tmp_path, "STR-004").status is Status.PASS


def test_a_history_satisfying_git_003_passes_its_verifier(tmp_path: Path):
    """The requirement says scanning the history finds no credential."""
    commit(tmp_path, "config.js", "const token = process.env.GITHUB_TOKEN;\n",
           "read the token from the environment")

    assert entropy.scan_history(tmp_path) == []


def test_a_history_violating_git_003_is_caught_by_its_verifier(tmp_path: Path):
    """The other direction, so the check is known to discriminate."""
    commit(tmp_path, "config.js",
           'const token = "ghp_A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOa";\n', "add config")

    assert entropy.scan_history(tmp_path)


# --------------------------------------------------------------------------
# boundary and forbidden are scoped per rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", DD_IDS)
def test_each_forbidden_list_has_real_items(rule_id: str):
    forbidden = get(rule_id).forbidden

    assert len(forbidden) >= 4, f"{rule_id} forbids only {len(forbidden)} things"
    for item in forbidden:
        assert len(item.split()) >= 4, f"{rule_id} has a vague entry: {item}"


def test_no_two_structural_rules_share_a_boundary():
    """Generic boilerplate repeated across rules is not a boundary."""
    structural = [get(r).boundary for r in DD_IDS if r.startswith("STR")]

    assert len(structural) == 4
    assert len(set(structural)) == 4
    # The two history rules do share one, because the risk really is identical.
    assert get("GIT-003").boundary == get("GIT-007").boundary


def test_forbidden_lists_are_not_copied_between_rule_families():
    struct = set(get("STR-001").forbidden)
    history = set(get("GIT-003").forbidden)

    assert not struct & history


def test_the_structural_boundaries_name_the_files_they_allow():
    assert "controller" in get("STR-001").boundary.lower()
    assert "service" in get("STR-001").boundary.lower()
    assert "route file" in get("STR-002").boundary.lower()
    assert "react root" in get("STR-003").boundary.lower()
    assert "router file" in get("STR-004").boundary.lower()


def test_the_history_boundary_carries_the_real_risk():
    """Shared branches, rotation order and untouched commits, per module 2.3."""
    forbidden = " ".join(get("GIT-003").forbidden).lower()

    assert "shared branch" in forbidden
    assert "force push" in forbidden
    assert "rotated" in forbidden
    assert "never carried the credential" in forbidden


def test_a_contract_with_a_thin_forbidden_list_is_refused():
    with pytest.raises(ContractError):
        Contract("con.x", "requirement", "boundary", ("only one thing",), "astchecks.check_helmet")


# --------------------------------------------------------------------------
# the Section 5.2 second contract form
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", DD_IDS)
def test_render_produces_every_second_form_field(rule_id: str):
    payload = render(issue(rule_id)).to_dict()

    assert set(payload) == {
        "rule_id", "action", "file_path", "violation", "requirement",
        "boundary", "forbidden", "constraint",
    }
    assert payload["action"] == ACTION == "author_within_constraint"
    assert payload["constraint"] == CONSTRAINT


def test_the_constraint_is_the_wording_from_the_document():
    assert CONSTRAINT == (
        "Author the minimal change that satisfies the requirement within the "
        "boundary. Nothing outside the boundary may be touched."
    )


def test_no_delegation_carries_fix_content():
    """ProdPilot supplies the boundary, not the text. Nothing is authored here."""
    for rule_id in DD_IDS:
        payload = render(issue(rule_id)).to_dict()
        for value in payload.values():
            assert "require(" not in value
            assert "function " not in value
            assert "app.use" not in value


def test_the_violation_comes_from_the_real_audit_finding():
    report = run(SAMPLES / "node_express_insecure")
    one = next(i for i in report.issues if i.rule_id == "STR-001")

    rendered = render(one)

    assert "line 4" in rendered.violation
    assert "invoiceController.js" in rendered.violation
    assert rendered.violation != one.rule.description


def test_the_violation_falls_back_to_the_rule_when_there_is_no_evidence():
    rule = get_rule("STR-001")
    bare = RuleResult(rule=rule, status=Status.FAIL, evidence=())

    assert violation(bare) == rule.description


def test_a_history_rule_targets_the_repository_not_a_commit_reference():
    """Its findings carry a commit ref, which is not a path an agent can open."""
    rule = get_rule("GIT-003")
    hit = Finding("GIT-003", Status.FAIL, "history:abc12345", None, "a secret is in history")
    one = RuleResult(rule=rule, status=Status.FAIL, evidence=(hit,))

    assert target(one) == REPO


def test_a_structural_rule_targets_the_file_the_audit_named():
    rendered = render(issue("STR-001", file="src/controllers/userController.js"))

    assert rendered.file_path == "src/controllers/userController.js"


def test_rendering_a_rule_of_another_fix_type_is_refused():
    with pytest.raises(ContractError):
        render(issue("SEC-002"))


def test_asking_for_an_unknown_rule_is_refused():
    with pytest.raises(ContractError):
        get("NOPE-000")


# --------------------------------------------------------------------------
# against real audits
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample,rule_id",
    [
        ("node_express_insecure", "STR-001"),
        ("node_express_insecure", "STR-002"),
        ("react_vite_app", "STR-003"),
        ("react_vite_noroute", "STR-004"),
    ],
)
def test_each_structural_rule_renders_from_a_real_violation(sample: str, rule_id: str):
    report = run(SAMPLES / sample)
    one = next((i for i in report.issues if i.rule_id == rule_id), None)

    assert one is not None, f"{sample} did not raise {rule_id}"
    rendered = render(one)
    assert rendered.file_path.endswith((".js", ".jsx"))
    assert rendered.violation.strip()
    assert rendered.requirement == get(rule_id).requirement


def test_rendering_is_deterministic():
    one = issue("STR-002")

    assert render(one).to_dict() == render(one).to_dict()


# --------------------------------------------------------------------------
# the integration point for 3.5
# --------------------------------------------------------------------------


def test_the_resolver_reports_resolved_only_on_the_verified_verdict():
    """This module cannot confirm an authored change, so it never decides alone."""
    resolve = resolver(lambda d: True)

    step = resolve(issue("STR-001"), 1)

    assert step.outcome is Outcome.RESOLVED
    assert "verified" in step.detail


def test_the_resolver_reports_unresolved_when_the_rule_still_fails():
    resolve = resolver(lambda d: False)

    step = resolve(issue("STR-001"), 1)

    assert step.outcome is Outcome.UNRESOLVED


def test_the_resolver_blocks_an_issue_another_module_owns():
    step = resolver(lambda d: True)(issue("SEC-002"), 1)

    assert step.outcome is Outcome.BLOCKED
    assert "not DYNAMIC-DELEGATED" in step.detail


def test_the_resolver_absorbs_a_dispatch_that_raises():
    def boom(delegation):
        raise RuntimeError("the tool call failed")

    step = resolver(boom)(issue("STR-001"), 1)

    assert step.outcome is Outcome.UNRESOLVED
    assert "the tool call failed" in step.detail


def test_the_resolver_receives_the_rendered_delegation():
    seen = []

    def capture(delegation):
        seen.append(delegation)
        return True

    resolver(capture)(issue("STR-003"), 1)

    assert len(seen) == 1
    assert seen[0].action == ACTION
    assert "getDerivedStateFromError" in seen[0].requirement


def test_the_resolver_drives_the_real_loop_controller():
    report = run(SAMPLES / "node_express_insecure")
    dd = [i for i in report.issues if i.rule.fix_type is FixType.DYNAMIC_DELEGATED]

    result = Loop(resolver(lambda d: True)).run(dd)

    assert len(result.resolved) == len(dd)
    assert result.review == ()


def test_a_change_that_never_satisfies_the_rule_exhausts_the_budget():
    result = Loop(resolver(lambda d: False)).run([issue("STR-001")])

    assert len(result.attempts) == 3
    assert len(result.review) == 1


def test_the_resolver_signature_matches_the_controller_contract():
    assert isinstance(resolver(lambda d: True)(issue("STR-004"), 2), Step)
