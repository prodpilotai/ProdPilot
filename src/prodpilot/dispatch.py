"""Fix dispatch and MCP tool wiring for the bounded agentic loop.

Scope is Phase 3 module 3.5. Modules 3.2, 3.3 and 3.4 each produce a fix
contract for one fix type. This module decides which of them owns a given issue,
carries the contract out to the IDE agent over MCP, and takes the agent's answer
back. It produces no fix content of its own.

It is not module 3.6. The verdict on whether a fix actually worked is computed
by verify.py, which re-runs the rule's own checker, and arrives here through the
seam described below. Nothing here computes it and nothing here defaults it.

How dispatch is decided
-----------------------
By the fix_type field on the rule, read from the frozen store. Section 5.3 lists
classification as the step before any fix is produced, and rules.py already
records that classification for all 50 rules, so nothing is re-derived here. An
issue whose fix type has no resolver is blocked, not guessed at.

The two contract forms
----------------------
Section 5.2 defines two. STATIC and DYNAMIC-PARAMETRIC both produce the first,
where ProdPilot determines the content, so both arrive here as a templates
Instruction. DYNAMIC-DELEGATED produces the second, where ProdPilot supplies
only the boundary, and arrives as a constraints Delegation. Form records which
one the agent is holding so it never has to infer that from the field names.

The two seams
-------------
agent is the MCP side. It carries one contract to the IDE agent and returns what
the agent says it did. That answer is a Claim, and the name is the point: it is
a self-report and it is recorded, never believed.

verify is module 3.6, verify.Verifier in practice. Given a rule id it re-runs
that rule's checker against the project on disk and answers whether the rule
passes now. Nothing in this module can answer that question, so nothing here
tries.

Why verify has no default
-------------------------
Section 5.3 states that agent self-report is never trusted, and 3.6 is the one
mechanism the whole system depends on for correctness. A default would make a
missing verifier invisible: the loop would report RESOLVED and no test would
fail. So Fixer requires one and raises without it, which keeps a wrong wiring a
loud failure rather than a quiet pass.

One fix must not break another
-------------------------------
The verifier answers for the rule being fixed and nothing else, so on its own
it would call a change resolved even if that change broke a rule which passed
before it. Registering a route above existing middleware can do exactly that.
So Fixer also audits the project before and after every change and compares
the two. A rule that passed before and fails after is a regression: the change
is reverted from the files it named, the rule being fixed is blocked for manual
review rather than retried, and the regression is recorded. Retrying would not
help, since a content contract renders the same change every time.

The comparison is a full audit, run only when the files actually changed, so a
change that touched nothing costs nothing extra.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from prodpilot import audit, constraints, extraction, templates
from prodpilot.astchecks import SKIP_DIRS
from prodpilot.audit import Report, RuleResult
from prodpilot.constraints import Delegation
from prodpilot.findings import Status
from prodpilot.rules import FixType, get_rule
from prodpilot.templates import Instruction

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    """Raised when an issue cannot be routed to a resolver."""


class Form(str, Enum):
    """Which of Section 5.2's two contract forms a fix is in."""

    CONTENT = "content"
    CONSTRAINT = "constraint"


# The mapping Section 5.2 sets out. fix_type comes from rules.py and is the only
# input, so classification lives in one place and is not repeated here.
FORMS: dict[FixType, Form] = {
    FixType.STATIC: Form.CONTENT,
    FixType.DYNAMIC_PARAMETRIC: Form.CONTENT,
    FixType.DYNAMIC_DELEGATED: Form.CONSTRAINT,
}


@dataclass(frozen=True)
class Fix:
    """One rendered contract, ready to travel to the agent."""

    rule_id: str
    fix_type: FixType
    form: Form
    body: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "fix_type": self.fix_type.value,
            "form": self.form.value,
            "contract": self.body,
        }


@dataclass(frozen=True)
class Claim:
    """What the agent says it did.

    Deliberately not called a result. Section 5.3 rules out acting on this, so
    it is carried for the record and for the delegated success rate Section 5.3
    asks to be tracked, and it never decides an outcome.
    """

    rule_id: str
    applied: bool
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "applied": self.applied,
            "summary": self.summary,
        }


# The MCP side: carry one contract to the IDE agent, return what it reports.
Agent = Callable[[Fix], Claim]

# Module 3.6: given a rule id, re-run that rule's checker and answer whether the
# rule passes now. verify.verifier(root) builds one bound to a project, the same
# way extraction.resolver binds a root.
Verify = Callable[[str], bool]


def form_of(fix_type: FixType) -> Form:
    """Which contract form a fix type produces."""
    found = FORMS.get(fix_type)
    if found is None:
        raise DispatchError(f"no contract form is defined for {fix_type.value}")
    return found


def as_fix(payload: Instruction | Delegation) -> Fix:
    """Wrap a rendered contract from 3.2, 3.3 or 3.4 for transport.

    The fix type is taken from the frozen store rather than from which dataclass
    arrived. STATIC and DYNAMIC-PARAMETRIC both render an Instruction, so the
    class alone cannot tell them apart, and rules.py is the source of truth for
    the distinction in any case.
    """
    if not isinstance(payload, (Instruction, Delegation)):
        raise DispatchError(f"{type(payload).__name__} is not a fix contract")
    rule = get_rule(payload.rule_id)
    if rule is None:
        raise DispatchError(f"{payload.rule_id} is not a rule in the store")
    return Fix(rule.rule_id, rule.fix_type, form_of(rule.fix_type), payload.to_dict())


def instruct(root: str | Path, issue: RuleResult) -> Fix:
    """Render the contract for one issue in the form its fix type requires.

    Routing only. The content comes from 3.2, 3.3 or 3.4 unchanged. Kept at
    module level because producing a contract needs neither seam, so the MCP
    tool that only hands one out does not have to hold an agent or a verifier
    it would never call.
    """
    fix_type = issue.rule.fix_type
    if fix_type is FixType.STATIC:
        return as_fix(templates.render(issue))
    if fix_type is FixType.DYNAMIC_PARAMETRIC:
        return as_fix(extraction.render(extraction.load(root), issue))
    if fix_type is FixType.DYNAMIC_DELEGATED:
        return as_fix(constraints.render(issue))
    raise DispatchError(f"{issue.rule_id} has no resolver for {fix_type.value}")


def failing(report: Report, rule_id: str) -> RuleResult:
    """The failing issue for one rule in an audit report.

    Raises when the rule is not failing, since handing back an issue that is
    already passing would produce a fix for a problem the project does not have.
    """
    for issue in report.issues:
        if issue.rule_id == rule_id:
            return issue
    raise DispatchError(f"{rule_id} is not a failing rule in this audit")


@dataclass(frozen=True)
class Regression:
    """A fix that broke rules which passed before it."""

    rule_id: str
    broke: tuple[str, ...]
    reverted: bool

    @property
    def detail(self) -> str:
        undone = ("the change was reverted" if self.reverted
                  else "the change was not reverted and needs a person")
        return (f"fixing {self.rule_id} broke {', '.join(self.broke)}, which passed "
                f"before it, so {undone}")

    def to_dict(self) -> dict[str, object]:
        return {"rule_id": self.rule_id, "broke": list(self.broke),
                "reverted": self.reverted, "detail": self.detail}


def statuses(report: Report) -> dict[str, Status]:
    """Every assessed rule's status in one audit report."""
    return {r.rule_id: r.status for r in report.results}


def standing(root: str | Path) -> dict[str, Status] | None:
    """Every rule's status in the project as it is on disk now.

    None when the project cannot be audited, in which case there is nothing to
    compare against and no regression can be claimed either way.
    """
    try:
        return statuses(audit.run(root))
    except (OSError, ValueError) as exc:
        logger.warning("cannot audit %s to guard against regressions: %s", root, exc)
        return None


def broken(before: dict[str, Status], after: dict[str, Status],
           rule_id: str) -> tuple[str, ...]:
    """Rules that passed before a change and fail after it, other than the one fixed."""
    return tuple(sorted(
        r for r, status in before.items()
        if status is Status.PASS and r != rule_id
        and after.get(r) in (Status.FAIL, Status.UNPARSED)
    ))


def fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    """Size and modification time of every project file, to tell if any changed."""
    out = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            try:
                stat = os.stat(os.path.join(folder, name))
            except OSError:
                continue
            out.append((os.path.join(folder, name), stat.st_size, stat.st_mtime_ns))
    return tuple(out)


def keep(root: Path, fix: Fix) -> dict[Path, bytes | None]:
    """The files a content contract can touch, as they are before it is applied."""
    kept: dict[Path, bytes | None] = {}
    for name in {str(fix.body.get("file_path") or ""), "package.json"} - {""}:
        path = root / name
        try:
            kept[path] = path.read_bytes() if path.is_file() else None
        except OSError:
            continue
    return kept


def restore(kept: dict[Path, bytes | None]) -> bool:
    """Put kept files back as they were, removing any that did not exist."""
    try:
        for path, data in kept.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
    except OSError as exc:
        logger.warning("could not revert a change: %s", exc)
        return False
    return True


def outcome_of(result):
    """Turn one verification verdict into the outcome the loop speaks.

    Kept here rather than in verify.py so the loop vocabulary stays on this
    side of the seam, and kept in one place so the in process loop and the MCP
    report tool can never drift into reading the same verdict differently.

    A verdict of SKIPPED means the rule could not be evaluated against this
    project at all, which no number of retries would change, so it blocks
    rather than spending the budget.
    """
    from prodpilot.findings import Status
    from prodpilot.loop import Outcome, Step

    if result.passed:
        return Step(Outcome.RESOLVED, result.reason)
    if result.status is Status.SKIPPED:
        return Step(Outcome.BLOCKED, result.reason)
    return Step(Outcome.UNRESOLVED, result.reason)


class Fixer:
    """Routes issues to the resolver that owns their fix type.

    Holds the three resolvers 3.2, 3.3 and 3.4 already provide and hands each of
    them the same apply step, so the per fix type behaviour those modules built
    stays exactly as they built it. This class adds routing and the agent round
    trip, and the regression guard, nothing else.
    """

    def __init__(self, root: str | Path, agent: Agent, verify: Verify,
                 guard: bool = True) -> None:
        if agent is None or verify is None:
            raise DispatchError("Fixer needs both an agent and a verify step")
        self.root = Path(root)
        self.agent = agent
        self.verify = verify
        self.guard = guard
        self.regressions: list[Regression] = []
        self.seen: tuple[tuple, dict[str, Status] | None] | None = None
        # Two parallel records of the same events, kept side by side so what the
        # agent said and what the verifier found can be compared directly. The
        # delegated success rate Section 5.3 asks for is measured from these.
        self.claims: list[Claim] = []
        self.verdicts: list[bool] = []
        self.resolvers = {
            FixType.STATIC: templates.resolver(self.apply),
            FixType.DYNAMIC_PARAMETRIC: extraction.resolver(self.apply, self.root),
            FixType.DYNAMIC_DELEGATED: constraints.resolver(self.apply),
        }

    def apply(self, payload: Instruction | Delegation) -> bool:
        """Send one contract to the agent, then return module 3.6's verdict.

        This is the callable 3.2, 3.3 and 3.4 each expect. The agent's claim is
        recorded and then set aside: the boolean returned is the verifier's,
        whatever the agent reported. A claim of failure is verified too, since
        an untrusted report is untrusted in both directions.

        The one exception is a regression. A change that broke another rule
        returns False whatever the verifier said, after reverting it, because a
        fix bought by breaking something else is not a fix.
        """
        fix = as_fix(payload)
        before = self.now() if self.guard else None
        kept = keep(self.root, fix) if before is not None else {}
        claim = self.agent(fix)
        self.claims.append(claim)
        verdict = self.verify(fix.rule_id)
        self.verdicts.append(verdict)
        logger.info(
            "%s: agent claims applied=%s, verifier says passes=%s",
            fix.rule_id,
            claim.applied,
            verdict,
        )
        if before is None:
            return verdict
        after = self.now()
        broke = broken(before, after, fix.rule_id) if after is not None else ()
        if not broke:
            return verdict
        # A constraint contract lets the agent choose what to edit, so the files
        # it named are not known to be all it touched. Only a content change,
        # which is fully determined, is put back automatically.
        reverted = fix.form is Form.CONTENT and restore(kept)
        found = Regression(fix.rule_id, broke, reverted)
        self.regressions.append(found)
        self.seen = None
        logger.warning("%s", found.detail)
        return False

    def now(self) -> dict[str, Status] | None:
        """Every rule's status as the files stand, audited only if they changed."""
        mark = fingerprint(self.root)
        if self.seen is None or self.seen[0] != mark:
            self.seen = (mark, standing(self.root))
        return self.seen[1]

    def instruct(self, issue: RuleResult) -> Fix:
        """The module level instruct, bound to this run's project root."""
        return instruct(self.root, issue)

    def resolve(self, issue: RuleResult, attempt: int):
        """The resolve step the 3.1 controller calls, routed by fix type."""
        from prodpilot.loop import Outcome, Step

        pick = self.resolvers.get(issue.rule.fix_type)
        if pick is None:
            return Step(
                Outcome.BLOCKED,
                f"{issue.rule_id} has no resolver for {issue.rule.fix_type.value}",
            )
        count = len(self.regressions)
        step = pick(issue, attempt)
        if len(self.regressions) > count:
            return Step(Outcome.BLOCKED, self.regressions[-1].detail)
        return step
