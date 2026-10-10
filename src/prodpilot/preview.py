"""Dry-run preview of the fixes the loop would make.

prodpilot fix --dry-run shows, for every failing rule, what the fix loop would
change, in the order the loop would reach it, and writes nothing to the project.

What is shown per fix type
--------------------------
STATIC and DYNAMIC-PARAMETRIC rules get a content contract, which fully decides
the change, so the preview shows it as a unified diff per file, a new file
against /dev/null. A parametric rule that refuses, because a value it needs is
ambiguous, shows the refusal and its candidates instead. A DYNAMIC-DELEGATED
rule's change is written by the IDE agent, so there is no diff to show: the
preview shows the constraint contract, the requirement, the files that may
change, what must stay unchanged and the checker that will decide.

How the order is found
----------------------
The project is copied to a temporary directory in the system temp folder, and
every content contract is applied to that copy, one at a time, in the loop's
order: priority first, then rule id. A contract that edits a file no earlier
change has created yet cannot be applied there. The loop meets the same thing:
the change fails verification, and the next cycle reaches it again after the
rule that creates the file has run. The preview does the same, up to the loop's
own ceiling of cycles, and shows such a change after the rule it depends on,
naming that rule. The temporary copy is removed when the preview ends, whether
or not it succeeded.

Secrets
-------
A diff of a rule that moves a credential out of source would show the
credential on its removed line. Every string the preview returns passes through
reporting.redact first, so a credential appears only as its first four
characters and its length, as in the reports.
"""

from __future__ import annotations

import difflib
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from prodpilot import astchecks, audit, dispatch, extraction, filechecks
from prodpilot.applying import write
from prodpilot.audit import RuleResult
from prodpilot.dispatch import Form
from prodpilot.entropy import scan_project
from prodpilot.reporting import redact
from prodpilot.loop import CEILING, order
from prodpilot.rules import FixType
from prodpilot.server import FIX_ERRORS

DIFF = "diff"
CONSTRAINT = "constraint"
REFUSED = "refused"

# Files a content change may touch besides its target: listed packages are
# declared in package.json and listed keys in .env.example.
SIDE_FILES = ("package.json", ".env.example")


class PreviewError(Exception):
    """The preview could not be produced."""


@dataclass(frozen=True)
class Planned:
    """What the loop would do for one failing rule."""

    rule_id: str
    priority: str
    fix_type: str
    kind: str
    cycle: int = 1
    diffs: tuple[tuple[str, str], ...] = ()
    contract: dict = field(default_factory=dict)
    reason: str = ""
    candidates: tuple[str, ...] = ()
    depends_on: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "priority": self.priority,
            "fix_type": self.fix_type,
            "kind": self.kind,
            "cycle": self.cycle,
            "diffs": [{"file": f, "diff": d} for f, d in self.diffs],
            "contract": self.contract,
            "reason": self.reason,
            "candidates": list(self.candidates),
            "depends_on": [{"rule_id": r, "file": f} for r, f in self.depends_on],
        }

    def text(self) -> str:
        head = f"{self.rule_id} ({self.priority}, {self.fix_type})"
        lines = [head]
        for rule_id, path in self.depends_on:
            lines.append(f"  depends on {rule_id}, which creates {path}")
        if self.kind == DIFF:
            if not self.diffs:
                lines.append("  no change needed: the content is already in place")
            for _, diff in self.diffs:
                lines.append(diff.rstrip("\n"))
        elif self.kind == CONSTRAINT:
            c = self.contract
            lines.append("  no diff: your IDE agent writes this change within the contract")
            lines.append(f"  requirement: {c.get('requirement', '')}")
            lines.append(f"  may change: {c.get('boundary', '')}")
            lines.append(f"  must not change: {c.get('forbidden', '')}")
            lines.append(f"  decided by: {c.get('checker', '')}")
        else:
            lines.append(f"  refused: {self.reason}")
            if self.candidates:
                lines.append(f"  candidates: {', '.join(self.candidates)}")
        return "\n".join(lines)


def checker_of(rule_id: str) -> str:
    """The name of the function that decides whether a rule passes."""
    for table in (astchecks.CHECKS, astchecks.PROJECT_CHECKS, filechecks.NODE_CHECKS,
                  filechecks.REACT_CHECKS):
        found = table.get(rule_id)
        if found is not None:
            return f"{found.__module__}.{found.__name__}"
    return f"the {rule_id} checker"


def unified(path: str, before: str | None, after: str) -> str:
    return "".join(difflib.unified_diff(
        (before or "").splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="/dev/null" if before is None else f"a/{path}", tofile=f"b/{path}"))


def read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def refusal(work: Path, issue: RuleResult, exc: Exception) -> tuple[str, tuple[str, ...]]:
    """The reason a contract was refused, with candidates where extraction names them."""
    if issue.rule.fix_type is FixType.DYNAMIC_PARAMETRIC and extraction.covers(issue.rule_id):
        got = extraction.extract(extraction.load(work), issue)
        if not got.ok:
            return got.reason, tuple(got.candidates)
    return str(exc), ()


def plan(root: str | Path, rule: str | None = None) -> list[Planned]:
    """Every change the fix loop would make, in loop order. Writes nothing to root.

    Raises NotADirectoryError when root is not a project, and PreviewError when
    rule is named but is not failing.
    """
    base = Path(root).resolve()
    report = audit.run(base)
    issues = list(order(report.issues))
    if rule is not None and rule not in {i.rule_id for i in issues}:
        raise PreviewError(f"{rule} is not failing in {base.name}, so there is nothing to preview")

    with tempfile.TemporaryDirectory(prefix="prodpilot-preview-") as holder:
        work = Path(holder) / "project"
        shutil.copytree(base, work, ignore=shutil.ignore_patterns("node_modules"))
        planned = simulate(work, issues)
    hidden = tuple({h.value for h in scan_project(base)})
    return [hide(p, hidden) for p in planned if rule is None or p.rule_id == rule]


def hide(planned: Planned, secrets: tuple[str, ...]) -> Planned:
    """The same plan with every credential the scanner found masked."""
    if not secrets:
        return planned
    contract = {k: redact(v, secrets) if isinstance(v, str) else v
                for k, v in planned.contract.items()}
    return replace(planned,
                   diffs=tuple((f, redact(d, secrets)) for f, d in planned.diffs),
                   contract=contract, reason=redact(planned.reason, secrets),
                   candidates=tuple(redact(c, secrets) for c in planned.candidates))


def simulate(work: Path, issues: list[RuleResult]) -> list[Planned]:
    """Apply every content contract to the copy, cycle by cycle, as the loop would."""
    created: dict[str, str] = {}
    out: list[Planned] = []
    pending = issues
    for cycle in range(1, CEILING + 1):
        waiting: list[RuleResult] = []
        for issue in pending:
            done = attempt(work, issue, cycle, created)
            if done is None:
                waiting.append(issue)
            else:
                out.append(done)
        if not waiting:
            break
        # Retried next cycle, once this cycle's changes are on disk, exactly as
        # the loop's next cycle would reach them.
        pending = waiting
    return out


def attempt(work: Path, issue: RuleResult, cycle: int,
            created: dict[str, str]) -> Planned | None:
    """Plan one rule against the copy. None when it has to wait for a later cycle."""
    kind = (issue.rule_id, issue.priority.value, issue.fix_type.value)
    try:
        fix = dispatch.instruct(work, issue)
    except FIX_ERRORS as exc:
        # A refusal is final: the loop sends the rule to manual review.
        reason, candidates = refusal(work, issue, exc)
        return Planned(*kind, REFUSED, cycle, reason=reason, candidates=candidates)

    target = fix.body.get("file_path", "")
    depends = ((created[target], target),) if target in created else ()
    if fix.form is Form.CONSTRAINT:
        contract = {**fix.body, "checker": checker_of(issue.rule_id)}
        return Planned(*kind, CONSTRAINT, cycle, contract=contract, depends_on=depends)

    watched = [target, *[s for s in SIDE_FILES if s != target]]
    before = {p: read(work / p) for p in watched}
    result = write(work, fix)
    if result.startswith(("no file at", "could not locate")):
        if cycle < CEILING:
            return None
        return Planned(*kind, REFUSED, cycle,
                       reason=f"the change cannot be applied: {result}")
    if result.startswith("unknown"):
        raise PreviewError(f"{issue.rule_id}: {result}")

    diffs = []
    for path in watched:
        after = read(work / path)
        if after is not None and after != before[path]:
            diffs.append((path, unified(path, before[path], after)))
    if before[target] is None:
        created[target] = issue.rule_id
    return Planned(*kind, DIFF, cycle, diffs=tuple(diffs), depends_on=depends)
