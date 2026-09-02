"""Independent re-verification of one rule after a fix.

Scope is Phase 3 module 3.6. Section 5.3 of the Complete Solution Document
states it in one line: ProdPilot re-runs the specific rule checker
independently, and agent self-report is never trusted. This module is that
re-run, and it is the single mechanism the rest of the system depends on for
correctness.

What independent means here
---------------------------
Nothing the agent said reaches this module. It takes a rule id and a project
path, finds the checker that raised the violation in the first place, and runs
that same function again over the files on disk. There is no report to read, no
claim to weigh, and no way for the fixing side to influence the answer other
than by actually changing the project.

Which checker runs
------------------
The one the rule already belongs to. Phase 2 built three tables and they are the
mapping, so nothing is duplicated here:

    astchecks.CHECKS          15 file scoped AST rules
    astchecks.PROJECT_CHECKS   6 cross file AST rules
    filechecks.NODE_CHECKS    12 file existence and entropy rules
    filechecks.REACT_CHECKS   17 file existence and entropy rules

That is 50, each rule in exactly one table. A rule with no checker is an error
rather than a pass.

Why the whole audit is not re-run
---------------------------------
Section 5.3 asks for the specific rule checker, and the loop calls this once per
attempt. Re-running all 50 rules to answer about one would be slower by a large
factor and would let an unrelated rule's failure confuse the answer. Only the
one rule's function runs.

Why a verdict is collapsed the same way the audit collapses one
---------------------------------------------------------------
A file scoped rule produces one finding per file, and the audit's reading is
that a rule fails if any file fails it. Verification has to agree with the audit
exactly, or the loop could resolve an issue the next audit raises again. So the
same collapse function from module 2.4 is used rather than a second opinion.

What counts as passing
----------------------
Only PASS. A rule that is UNPARSED, or that does not apply to this project's
stack, or that no checker produced a finding for, is not a pass. The loop treats
a false pass as a fixed issue and moves on, so every uncertain case fails
closed and stays in the loop or goes to manual review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from prodpilot import astchecks, filechecks
from prodpilot.audit import verdict
from prodpilot.detection import DetectionError, detect_stack
from prodpilot.findings import Finding, Status
from prodpilot.rules import Rule, get_rule

logger = logging.getLogger(__name__)


class VerifyError(Exception):
    """Raised when a rule cannot be verified at all."""


class Kind(str, Enum):
    """Which of Phase 2's three check tables owns a rule."""

    AST_FILE = "ast_file"
    AST_PROJECT = "ast_project"
    FILE = "file"


@dataclass(frozen=True)
class Verdict:
    """What the checker found when it was run again."""

    rule_id: str
    status: Status
    findings: tuple[Finding, ...] = ()
    reason: str = ""

    @property
    def passed(self) -> bool:
        """Only a PASS is a pass. Everything else keeps the issue open."""
        return self.status is Status.PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "passed": self.passed,
            "reason": self.reason,
            "locations": [f.to_dict() for f in self.findings],
        }


def checker(rule_id: str) -> tuple[Kind, object]:
    """The Phase 2 function that decides one rule, and which table it came from.

    Raises rather than returning None. A rule the audit can raise but nothing
    can re-check would be an issue the loop could never close, and silence
    would hide that.
    """
    if rule_id in astchecks.CHECKS:
        return Kind.AST_FILE, astchecks.CHECKS[rule_id]
    if rule_id in astchecks.PROJECT_CHECKS:
        return Kind.AST_PROJECT, astchecks.PROJECT_CHECKS[rule_id]
    for table in (filechecks.NODE_CHECKS, filechecks.REACT_CHECKS):
        if rule_id in table:
            return Kind.FILE, table[rule_id]
    raise VerifyError(f"no checker exists for {rule_id}")


def applies(rule: Rule, root: Path) -> bool:
    """Whether a rule belongs to the stack this project actually is.

    A React rule run against a Node project would answer about something the
    project was never meant to satisfy, so it is refused rather than answered.
    """
    try:
        found = detect_stack(root)
    except DetectionError as exc:
        raise VerifyError(f"cannot detect the stack of {root}: {exc}") from exc
    return found.is_supported and rule.stack is found.stack


def check(root: str | Path, rule_id: str) -> Verdict:
    """Re-run one rule's own checker against a project and report the result.

    Reads the project from disk every time. The loop calls this after a change
    has been made, so a cached read would answer about the project as it was
    before the fix, which is the one thing this must never do.
    """
    rule = get_rule(rule_id)
    if rule is None:
        raise VerifyError(f"{rule_id} is not a rule in the store")

    base = Path(root)
    if not base.is_dir():
        raise VerifyError(f"not a project directory: {base}")

    kind, fn = checker(rule_id)
    if not applies(rule, base):
        return Verdict(
            rule_id,
            Status.SKIPPED,
            reason=f"{rule_id} applies to {rule.stack.value}, which this project is not",
        )

    try:
        findings = run_check(kind, fn, base, rule_id)
    except (OSError, NotADirectoryError) as exc:
        raise VerifyError(f"cannot read {base} to verify {rule_id}: {exc}") from exc

    status = verdict(findings)
    hits = tuple(f for f in findings if f.status is status)
    result = Verdict(rule_id, status, hits, reason_for(rule_id, status, hits))
    logger.info("verified %s in %s: %s", rule_id, base, status.value)
    return result


def run_check(kind: Kind, fn, root: Path, rule_id: str) -> list[Finding]:
    """Call one Phase 2 checker with the inputs its table's signature expects."""
    if kind is Kind.AST_FILE:
        src = astchecks.load_sources(root)
        return [fn(tree, name) for name, tree in src.trees]
    if kind is Kind.AST_PROJECT:
        return [fn(astchecks.load_sources(root), rule_id)]
    return [fn(filechecks.load(root), rule_id)]


def reason_for(rule_id: str, status: Status, hits: tuple[Finding, ...]) -> str:
    """One line saying why, taken from the checker rather than written here."""
    if status is Status.PASS:
        return f"{rule_id} passes"
    if not hits:
        return f"no check produced a result for {rule_id}"
    first = hits[0]
    where = first.file or "the project"
    if first.line:
        where += f" line {first.line}"
    return f"{first.detail} ({where})"


class Verifier:
    """The Verify seam dispatch.py requires, bound to one project.

    Callable with a rule id and answering with a plain bool, which is the shape
    Fixer and the three resolvers already expect. The full verdict for the last
    call is kept on last, so a caller that wants the reason does not have to run
    the checker a second time to get it.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.last: Verdict | None = None

    def __call__(self, rule_id: str) -> bool:
        self.last = check(self.root, rule_id)
        return self.last.passed


def verifier(root: str | Path) -> Verifier:
    """Build the verifier for one project."""
    return Verifier(root)
