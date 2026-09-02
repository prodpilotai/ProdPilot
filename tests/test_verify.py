"""Independent re-verification tests for Phase 3 module 3.6.

This module is the one the correctness of everything else rests on, so the
coverage standard is every rule, not a sample. Each of the 50 rules is checked
twice: against a project where the violation is present, which must fail, and
against a project where it is genuinely absent, which must pass. A checker that
only ever failed, or only ever passed, would look identical to a working one
under any weaker test.

Most fixtures are the sample projects the audit tests already use. Eight rules
had no fixture that shows the violation, and two had none that shows it absent,
so those are built here, in full, rather than left uncovered.

Independence is proven separately. The verifier is given no access to the
agent's report by construction, and the tests drive the real Fixer with an agent
that claims success while the project is untouched, for all three fix types.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from apply import applier
from prodpilot import astchecks, filechecks
from prodpilot.audit import run
from prodpilot.dispatch import Claim, Fixer, Form, outcome_of
from prodpilot.findings import Status
from prodpilot.loop import Loop, Outcome
from prodpilot.rules import ALL_RULES, FixType, get_rule
from prodpilot.verify import Kind, Verdict, VerifyError, check, checker, verifier

SAMPLES = Path(__file__).resolve().parent / "samples"
IDS = [r.rule_id for r in ALL_RULES]
BUILT = "built"

NODE_PKG = {
    "name": "svc",
    "version": "1.0.0",
    "dependencies": {"express": "^4.18.0"},
    "scripts": {"start": "node src/server.js"},
}
REACT_PKG = {
    "name": "web",
    "version": "1.0.0",
    "dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0"},
    "devDependencies": {"vite": "^5.0.0"},
    "scripts": {"dev": "vite", "build": "vite build"},
}
SECRET = 'const key = "sk_live_9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOaXcVbN";\n'


def make(root: Path, pkg: dict, files: dict[str, str]) -> Path:
    """A minimal project of one stack, with the files a rule needs."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


def commit(root: Path, message: str) -> Path:
    """Put the project into a real repository with one commit."""
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return root


# --------------------------------------------------------------------------
# fixtures for the rules no sample project covers
# --------------------------------------------------------------------------


def db_hardcoded(root: Path) -> Path:
    return make(root, NODE_PKG, {"src/db.js": (
        'const { Pool } = require("pg");\n'
        'const pool = new Pool({ host: "db.internal", user: "admin", password: "hunter2" });\n'
        "module.exports = pool;\n")})


def no_build_script(root: Path) -> Path:
    pkg = {k: v for k, v in REACT_PKG.items() if k != "scripts"}
    return make(root, {**pkg, "scripts": {"dev": "vite"}}, {"src/main.jsx": "export default null;\n"})


def env_uncovered(root: Path) -> Path:
    """Reads a Vite env key with no .env.example declaring it."""
    return make(root, REACT_PKG, {
        "src/main.jsx": "const url = import.meta.env.VITE_API_URL;\nexport default url;\n"})


def host_literal(root: Path) -> Path:
    """A request to a literal host, which fails ENV-004 and ENV-005 both."""
    return make(root, REACT_PKG, {
        "src/api.js": 'fetch("https://api.example.com/v1/users").then((r) => r.json());\n'})


def secret_in_source(root: Path) -> Path:
    return make(root, REACT_PKG, {"src/keys.js": SECRET})


def node_secret_history(root: Path) -> Path:
    return commit(make(root, NODE_PKG, {"src/server.js": SECRET}), "add the key")


def node_clean_history(root: Path) -> Path:
    return commit(
        make(root, NODE_PKG, {"src/server.js": "const key = process.env.API_KEY;\n"}),
        "read the key from the environment")


def react_secret_history(root: Path) -> Path:
    return commit(make(root, REACT_PKG, {"src/main.jsx": SECRET}), "add the key")


def react_clean_history(root: Path) -> Path:
    return commit(
        make(root, REACT_PKG, {"src/main.jsx": "const key = import.meta.env.VITE_KEY;\n"}),
        "read the key from the environment")


FAIL_BUILDERS = {
    "CON-001": db_hardcoded,
    "BLD-011": no_build_script,
    "ENV-003": env_uncovered,
    "ENV-004": host_literal,
    "ENV-005": host_literal,
    "SCR-004": secret_in_source,
    "GIT-003": node_secret_history,
    "GIT-007": react_secret_history,
}

PASS_BUILDERS = {
    "GIT-003": node_clean_history,
    "GIT-007": react_clean_history,
}


# --------------------------------------------------------------------------
# where each rule's violation is present, and where it is absent
# --------------------------------------------------------------------------

FAILS = {
    "BLD-001": "node_express_insecure", "BLD-002": "node_express_insecure",
    "ENV-001": "amb_two_drivers", "GIT-001": "node_express_insecure",
    "BLD-003": "node_express_insecure", "GIT-002": "node_express_insecure",
    "SCR-001": "node_express_insecure", "BLD-004": "node_express_secrets",
    "BLD-005": "node_express_insecure", "BLD-006": "node_express_insecure",
    "SEC-001": "node_express_insecure", "SEC-002": "node_express_insecure",
    "SEC-003": "node_express_insecure", "SEC-004": "node_express_insecure",
    "SCR-002": "node_express_secrets", "ENV-002": "node_express_insecure",
    "CON-001": BUILT, "CON-002": "amb_two_drivers",
    "API-001": "node_express_insecure", "API-002": "node_express_insecure",
    "API-003": "node_express_insecure", "STR-001": "node_express_insecure",
    "STR-002": "node_express_insecure", "OBS-001": "node_express_insecure",
    "OBS-002": "node_express_insecure", "OBS-003": "node_express_insecure",
    "OBS-004": "node_express_insecure", "GIT-003": BUILT,
    "BLD-007": "react_vite_app", "BLD-008": "react_vite_app",
    "BLD-009": "react_vite_app", "ENV-003": BUILT,
    "GIT-004": "react_vite_app", "BLD-010": "react_vite_app",
    "GIT-005": "react_vite_app", "GIT-006": "react_vite_app",
    "SCR-003": "react_vite_app", "BLD-011": BUILT,
    "BLD-012": "react_vite_app", "BLD-013": "react_vite_app",
    "SEC-005": "react_vite_app", "OBS-005": "react_vite_app",
    "OBS-006": "react_vite_app", "ENV-004": BUILT,
    "ENV-005": BUILT, "SCR-004": BUILT,
    "SEC-006": "react_vite_app", "STR-003": "react_vite_app",
    "STR-004": "react_vite_noroute", "GIT-007": BUILT,
}

PASSES = {
    "BLD-001": "node_express_ready", "BLD-002": "node_express_ready",
    "ENV-001": "node_express_hardened", "GIT-001": "node_express_ready",
    "BLD-003": "node_express_ready", "GIT-002": "node_express_ready",
    "SCR-001": "node_express_ready", "BLD-004": "node_express_ready",
    "BLD-005": "node_express_ready", "BLD-006": "node_express_ready",
    "SEC-001": "node_express_ready", "SEC-002": "node_express_ready",
    "SEC-003": "node_express_hardened", "SEC-004": "node_express_hardened",
    "SCR-002": "node_express_ready", "ENV-002": "node_express_ready",
    "CON-001": "node_express_hardened", "CON-002": "node_express_hardened",
    "API-001": "node_express_hardened", "API-002": "node_express_hardened",
    "API-003": "node_express_hardened", "STR-001": "node_express_secure",
    "STR-002": "node_express_hardened", "OBS-001": "node_express_ready",
    "OBS-002": "node_express_hardened", "OBS-003": "node_express_hardened",
    "OBS-004": "node_express_hardened", "GIT-003": BUILT,
    "BLD-007": "react_vite_ready", "BLD-008": "react_vite_ready",
    "BLD-009": "react_vite_ready", "ENV-003": "react_vite_hardened",
    "GIT-004": "react_vite_ready", "BLD-010": "react_vite_ready",
    "GIT-005": "react_vite_ready", "GIT-006": "react_vite_ready",
    "SCR-003": "react_vite_ready", "BLD-011": "react_vite_ready",
    "BLD-012": "react_vite_ready", "BLD-013": "react_vite_ready",
    "SEC-005": "react_vite_ready", "OBS-005": "react_vite_ready",
    "OBS-006": "react_vite_ready", "ENV-004": "react_vite_hardened",
    "ENV-005": "react_vite_ready", "SCR-004": "react_vite_ready",
    "SEC-006": "react_vite_ready", "STR-003": "react_vite_hardened",
    "STR-004": "react_vite_hardened", "GIT-007": BUILT,
}


def source(rule_id: str, table: dict, builders: dict, tmp_path: Path) -> Path:
    """The project this rule is checked against, built if no sample shows it."""
    named = table[rule_id]
    if named is BUILT:
        return builders[rule_id](tmp_path / rule_id.lower())
    return SAMPLES / named


# --------------------------------------------------------------------------
# every rule, both directions
# --------------------------------------------------------------------------


def test_the_two_tables_cover_every_rule_exactly_once():
    """The coverage claim is enforced here rather than counted by hand."""
    assert sorted(FAILS) == sorted(IDS) == sorted(PASSES)
    assert len(IDS) == 50


def test_every_built_fixture_is_used():
    assert set(FAIL_BUILDERS) == {r for r, v in FAILS.items() if v is BUILT}
    assert set(PASS_BUILDERS) == {r for r, v in PASSES.items() if v is BUILT}


@pytest.mark.parametrize("rule_id", IDS)
def test_the_violation_is_caught(rule_id: str, tmp_path: Path):
    """A project with the violation present must fail this rule."""
    root = source(rule_id, FAILS, FAIL_BUILDERS, tmp_path)

    result = check(root, rule_id)

    assert result.status is Status.FAIL, f"{rule_id} in {root.name}: {result.reason}"
    assert result.passed is False
    assert result.reason.strip()


@pytest.mark.parametrize("rule_id", IDS)
def test_the_fixed_version_passes(rule_id: str, tmp_path: Path):
    """A project without the violation must pass this rule."""
    root = source(rule_id, PASSES, PASS_BUILDERS, tmp_path)

    result = check(root, rule_id)

    assert result.status is Status.PASS, f"{rule_id} in {root.name}: {result.reason}"
    assert result.passed is True


# --------------------------------------------------------------------------
# the verifier runs the checker the audit ran
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", IDS)
def test_every_rule_has_a_checker(rule_id: str):
    kind, fn = checker(rule_id)

    assert isinstance(kind, Kind)
    assert callable(fn)


def test_the_checkers_come_from_the_phase_two_tables():
    """Nothing is reimplemented here, so each function must be the audit's own."""
    for rule_id in IDS:
        kind, fn = checker(rule_id)
        if kind is Kind.AST_FILE:
            assert fn is astchecks.CHECKS[rule_id]
        elif kind is Kind.AST_PROJECT:
            assert fn is astchecks.PROJECT_CHECKS[rule_id]
        else:
            table = filechecks.NODE_CHECKS if rule_id in filechecks.NODE_CHECKS else filechecks.REACT_CHECKS
            assert fn is table[rule_id]


@pytest.mark.parametrize(
    "sample",
    ["node_express_insecure", "node_express_secure", "node_express_hardened",
     "react_vite_app", "react_vite_ready"],
)
def test_the_verifier_agrees_with_the_full_audit(sample: str):
    """A disagreement would let the loop close an issue the next audit reopens."""
    root = SAMPLES / sample
    report = run(root)

    for one in report.results:
        assert check(root, one.rule_id).status is one.status, one.rule_id


def test_a_rule_for_the_other_stack_is_not_a_pass():
    """Failing closed matters most where a rule cannot be evaluated at all."""
    result = check(SAMPLES / "node_express_insecure", "BLD-007")

    assert result.status is Status.SKIPPED
    assert result.passed is False
    assert "react_vite" in result.reason


def test_an_unknown_rule_is_refused():
    with pytest.raises(VerifyError):
        check(SAMPLES / "node_express_insecure", "NOPE-000")


def test_a_path_that_is_not_a_project_is_refused():
    with pytest.raises(VerifyError):
        check(SAMPLES / "no-such-project", "SEC-002")


def test_a_rule_with_no_checker_is_refused():
    with pytest.raises(VerifyError):
        checker("NOPE-000")


def test_an_unreadable_project_is_refused(tmp_path: Path):
    """An unrecognised project cannot be verified, and must not report a pass."""
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")

    result = check(tmp_path, "SEC-002")

    assert result.passed is False


def test_the_verifier_reads_the_project_again_on_every_call(tmp_path: Path):
    """A cached read would answer about the project as it was before the fix."""
    root = tmp_path / "svc"
    make(root, NODE_PKG, {"src/server.js": (
        'const express = require("express");\n'
        "const app = express();\n"
        'app.get("/", (req, res) => res.json({}));\n'
        "app.listen(3000);\n")})
    check_it = verifier(root)
    assert check_it("SEC-002") is False

    (root / "src" / "server.js").write_text(
        'const express = require("express");\n'
        'const helmet = require("helmet");\n'
        "const app = express();\n"
        "app.use(helmet());\n"
        'app.get("/", (req, res) => res.json({}));\n'
        "app.listen(3000);\n", encoding="utf-8")

    assert check_it("SEC-002") is True


def test_the_verifier_keeps_the_last_verdict_for_the_caller():
    check_it = verifier(SAMPLES / "node_express_insecure")

    check_it("SEC-002")

    assert isinstance(check_it.last, Verdict)
    assert check_it.last.rule_id == "SEC-002"
    assert "helmet" in check_it.last.reason


def test_a_verdict_serialises_with_its_reason_and_locations():
    payload = check(SAMPLES / "node_express_insecure", "SEC-002").to_dict()

    assert set(payload) == {"rule_id", "status", "passed", "reason", "locations"}
    assert payload["passed"] is False
    assert payload["locations"]


# --------------------------------------------------------------------------
# a verdict becomes an outcome
# --------------------------------------------------------------------------


def test_a_pass_becomes_resolved():
    step = outcome_of(check(SAMPLES / "node_express_ready", "SEC-002"))

    assert step.outcome is Outcome.RESOLVED


def test_a_failure_becomes_unresolved_so_the_budget_is_spent():
    step = outcome_of(check(SAMPLES / "node_express_insecure", "SEC-002"))

    assert step.outcome is Outcome.UNRESOLVED
    assert "helmet" in step.detail


def test_a_rule_that_cannot_be_evaluated_blocks_rather_than_retries():
    step = outcome_of(check(SAMPLES / "node_express_insecure", "BLD-007"))

    assert step.outcome is Outcome.BLOCKED


# --------------------------------------------------------------------------
# independence from the agent's report, for all three fix types
# --------------------------------------------------------------------------


def liar(applied: bool = True):
    """An agent that reports whatever it is told while changing nothing."""
    return lambda fix: Claim(fix.rule_id, applied, "claims the fix is done")


@pytest.mark.parametrize(
    "rule_id,fix_type",
    [
        ("SEC-002", FixType.STATIC),
        ("BLD-001", FixType.DYNAMIC_PARAMETRIC),
        ("STR-001", FixType.DYNAMIC_DELEGATED),
    ],
)
def test_a_false_claim_of_success_does_not_resolve(rule_id: str, fix_type: FixType):
    """Section 5.3 with the real verifier, for each of the three fix types."""
    root = SAMPLES / "node_express_insecure"
    assert get_rule(rule_id).fix_type is fix_type
    issue = next(i for i in run(root).issues if i.rule_id == rule_id)
    fixer = Fixer(root, liar(applied=True), verifier(root))

    step = fixer.resolve(issue, 1)

    assert fixer.claims[0].applied is True
    assert fixer.verdicts[0] is False
    assert step.outcome is Outcome.UNRESOLVED


@pytest.mark.parametrize("rule_id", ["SEC-002", "BLD-001", "STR-001"])
def test_a_false_claim_of_failure_does_not_block_a_real_pass(rule_id: str, tmp_path: Path):
    """The other direction: a rule that passes resolves whatever the agent says."""
    root = source(rule_id, PASSES, PASS_BUILDERS, tmp_path)
    issue = next(i for i in run(SAMPLES / "node_express_insecure").issues if i.rule_id == rule_id)
    fixer = Fixer(root, liar(applied=False), verifier(root))

    step = fixer.resolve(issue, 1)

    assert fixer.claims[0].applied is False
    assert step.outcome is Outcome.RESOLVED


def test_the_agent_cannot_reach_the_verifier_at_all():
    """Independence by construction: check takes a path and a rule id, nothing else."""
    import inspect

    assert list(inspect.signature(check).parameters) == ["root", "rule_id"]
    assert list(inspect.signature(verifier).parameters) == ["root"]


# --------------------------------------------------------------------------
# the full loop, end to end, with real verification
# --------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A writable copy of the seeded broken repo."""
    root = tmp_path / "project"
    shutil.copytree(SAMPLES / "node_express_insecure", root)
    return root


def test_the_loop_improves_a_real_project_under_real_verification(project: Path):
    before = run(project)
    fixer = Fixer(project, applier(project), verifier(project))

    result = Loop(fixer.resolve).run(before.issues)
    after = run(project)

    assert result.resolved
    assert after.score > before.score
    assert len(after.issues) < len(before.issues)


def test_every_resolved_rule_actually_passes_afterwards(project: Path):
    """The claim the loop makes about its own work, checked against the audit."""
    fixer = Fixer(project, applier(project), verifier(project))

    result = Loop(fixer.resolve).run(run(project).issues)
    after = {r.rule_id: r.status for r in run(project).results}

    for rule_id in result.resolved:
        assert after[rule_id] is Status.PASS, rule_id


def test_an_unfixable_rule_exhausts_its_budget_and_is_recorded(project: Path):
    """A delegated rule with no author present cannot resolve, and must not."""
    fixer = Fixer(project, applier(project), verifier(project))

    result = Loop(fixer.resolve).run(run(project).issues)
    reviewed = {r.rule_id: r for r in result.review}

    assert "STR-001" in reviewed
    assert reviewed["STR-001"].attempts == 3
    assert reviewed["STR-001"].reason.strip()
    assert reviewed["STR-001"].fix_type == FixType.DYNAMIC_DELEGATED.value


def test_every_review_record_carries_a_location_and_a_reason(project: Path):
    """Section 5.3 requires both, so both are checked on a real run."""
    fixer = Fixer(project, applier(project), verifier(project))

    result = Loop(fixer.resolve).run(run(project).issues)

    assert result.review
    for record in result.review:
        assert record.rule_id and record.requirement.strip()
        assert record.reason.strip()
        assert record.attempts == 3


def test_no_verdict_in_the_run_came_from_the_agent(project: Path):
    """Every outcome traces to a checker call, one per attempt, never to a claim."""
    fixer = Fixer(project, applier(project), verifier(project))

    result = Loop(fixer.resolve).run(run(project).issues)

    assert len(fixer.verdicts) == len(fixer.claims) == len(result.attempts)
    resolved = [c.rule_id for c, v in zip(fixer.claims, fixer.verdicts) if v]
    assert sorted(set(resolved)) == sorted(result.resolved)


def test_the_loop_respects_its_bounds_under_a_forced_failure(project: Path):
    """Phase 3's exit criterion, with nothing standing in for verification.

    A blocked issue is recorded on its first attempt rather than retried, since
    3.2 and 3.3 block where another attempt would re-read the same project and
    refuse identically. So the bound is at most 3, and exactly 3 for every issue
    that was actually retryable.
    """
    issues = run(project).issues
    fixer = Fixer(project, liar(applied=True), verifier(project))

    result = Loop(fixer.resolve).run(issues)
    retried = [a.rule_id for a in result.attempts if a.outcome is Outcome.UNRESOLVED]

    assert result.resolved == ()
    assert len(result.review) == len(issues)
    assert all(1 <= r.attempts <= 3 for r in result.review)
    assert max(r.attempts for r in result.review) == 3
    assert result.iterations <= 5
    assert len(fixer.verdicts) == len(retried)


# --------------------------------------------------------------------------
# Phase 3 exit criteria, checked exactly as Section 4 states them
# --------------------------------------------------------------------------

# A change that satisfies the STR-001 and STR-002 constraints for this one
# fixture. Written by hand because a constraint contract carries no content and
# no agent can author one inside a test. Everything else in the run is real,
# including the verdict on whether this change satisfies the rules.
SERVICE = '''const Invoice = require("../models/Invoice");

async function list() {
  return Invoice.find({ paid: false });
}

module.exports = { list };
'''

CONTROLLER = '''const invoiceService = require("../services/invoiceService");

async function list(req, res) {
  const invoices = await invoiceService.list();
  res.json({ invoices });
}

module.exports = { list };
'''

ROUTES = '''const express = require("express");
const controller = require("../controllers/invoiceController");

const router = express.Router();

router.get("/", controller.list);

module.exports = router;
'''

AUTHORED = {
    "STR-001": {
        "src/services/invoiceService.js": SERVICE,
        "src/controllers/invoiceController.js": CONTROLLER,
    },
    "STR-002": {"src/routes/invoiceRoutes.js": ROUTES},
}


def author(root: Path):
    """An agent that applies the hand written change for a delegated rule."""
    execute = applier(root)

    def agent(fix):
        files = AUTHORED.get(fix.rule_id)
        if fix.form is not Form.CONSTRAINT or not files:
            return execute(fix)
        for name, body in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        return Claim(fix.rule_id, True, "authored within the constraint")

    return agent


def test_all_three_fix_types_resolve_on_the_seeded_repo(project: Path):
    """Phase 3 exit criterion one, with the verdict from the real checkers."""
    fixer = Fixer(project, author(project), verifier(project))

    result = Loop(fixer.resolve).run(run(project).issues)
    kinds = {get_rule(r).fix_type for r in result.resolved}

    assert kinds == set(FixType), f"only resolved {sorted(k.value for k in kinds)}"
    assert "STR-001" in result.resolved
    assert "STR-002" in result.resolved


def test_a_deliberately_broken_fix_is_caught(project: Path):
    """Phase 3 exit criterion two.

    The change imports helmet and registers it, but after the routes, which is
    the mistake the rule exists to catch. It looks applied and it is not a fix.
    """

    def broken(fix):
        target = project / "src" / "server.js"
        text = target.read_text(encoding="utf-8")
        target.write_text(
            'const helmet = require("helmet");\n'
            + text.rstrip("\n")
            + "\napp.use(helmet());\n",
            encoding="utf-8",
        )
        return Claim(fix.rule_id, True, "registered helmet")

    issue = next(i for i in run(project).issues if i.rule_id == "SEC-002")
    fixer = Fixer(project, broken, verifier(project))

    step = fixer.resolve(issue, 1)

    assert fixer.claims[0].applied is True
    assert step.outcome is Outcome.UNRESOLVED
    assert "helmet" in check(project, "SEC-002").reason

