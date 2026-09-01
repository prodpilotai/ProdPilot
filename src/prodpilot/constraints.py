"""DYNAMIC-DELEGATED constraint contracts for the bounded agentic loop.

Scope is Phase 3 module 3.4. This module builds the constraint sent to the
development environment's own agent for the 6 rules the frozen store classifies
DYNAMIC-DELEGATED, in the second contract form from Section 5.2.

It produces no STATIC template (3.2), no DYNAMIC-PARAMETRIC extraction (3.3), no
MCP wiring (3.5) and no verification (3.6).

What this module does not do
----------------------------
It writes no fix. That is the whole point of the fix type. Section 4.1 reserves
DYNAMIC-DELEGATED for changes needing judgement about how a specific codebase is
organised, and Section 5.2 is explicit that ProdPilot supplies the boundary, not
the text. Everything here describes the shape of an acceptable change and the
edges the agent may not cross. The agent, which already holds full codebase
context, authors the content.

Why the requirement is written as a condition
---------------------------------------------
Section 5.2 states that the requirement is a checkable condition on purpose,
because that condition is exactly what the independent re-verification step
checks afterward. So each requirement here is phrased as a state that either
holds or does not, in the same terms the existing checker for that rule already
evaluates: the database method names check_service_layer looks for, the eight
statement threshold check_thin_routes applies, the two lifecycle hooks
check_boundary requires, the catch all path literals check_catch_all accepts,
and the history scan scan_history performs.

Each contract names its verifier for that reason. The name is the function that
will decide the outcome once 3.6 is wired, and a test resolves every one of them
to prove the requirement is tied to something real rather than to prose.

Why boundary and forbidden are written per rule
-----------------------------------------------
A generic boundary would be worse than none, because an agent given a vague edge
will pick its own. Each contract names the specific files that may change and
the specific things that must not, drawn from what the corresponding check
actually inspects. The two history rules carry a different kind of boundary
again, since rewriting published commits is dangerous in ways no code change is.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot.audit import RuleResult
from prodpilot.rules import FixType

logger = logging.getLogger(__name__)

ACTION = "author_within_constraint"

CONSTRAINT = (
    "Author the minimal change that satisfies the requirement within the "
    "boundary. Nothing outside the boundary may be touched."
)

# A history rule changes the repository rather than one file, and the contract
# still needs a file_path. The repository root is the honest answer, and the
# boundary text carries the real scope.
REPO = "."


class ContractError(Exception):
    """Raised when a rule is routed here that this module does not own."""


@dataclass(frozen=True)
class Contract:
    """The fixed part of a delegation: what must become true, and the edges.

    verifier names the function that decides the outcome once 3.6 is wired. It
    is recorded so the requirement stays tied to a real check rather than to
    prose that reads well and confirms nothing.
    """

    template_id: str
    requirement: str
    boundary: str
    forbidden: tuple[str, ...]
    verifier: str

    def __post_init__(self) -> None:
        if len(self.forbidden) < 2:
            raise ContractError(
                f"{self.template_id}: a forbidden list of fewer than two items is "
                f"not a boundary"
            )


@dataclass(frozen=True)
class Delegation:
    """The Section 5.2 contract, second form."""

    rule_id: str
    file_path: str
    violation: str
    requirement: str
    boundary: str
    forbidden: str
    action: str = ACTION
    constraint: str = CONSTRAINT

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "action": self.action,
            "file_path": self.file_path,
            "violation": self.violation,
            "requirement": self.requirement,
            "boundary": self.boundary,
            "forbidden": self.forbidden,
            "constraint": self.constraint,
        }


# --------------------------------------------------------------------------
# the contracts, one per delegated rule
# --------------------------------------------------------------------------

CONTRACTS: dict[str, Contract] = {
    "STR-001": Contract(
        template_id="con.node.service_layer",
        requirement=(
            "No database call remains in this controller file. After the change, "
            "no call in this file may have a driver method name such as find, "
            "findOne, save, create, insertOne, updateOne, deleteOne, aggregate, "
            "query or execute on a model or on a driver object such as db, pool, "
            "prisma or knex. Those calls live in a module under a services "
            "directory, and the controller reaches them by calling that module."
        ),
        boundary=(
            "This controller file, and one service module it may create or extend "
            "under a services directory beside it. No other file."
        ),
        forbidden=(
            "the exported names of this controller and the order they are exported in",
            "the (req, res, next) signature of every exported handler",
            "the HTTP status codes and response body shape each handler sends",
            "any route registration in any router file",
            "the behaviour of any query, which must move unchanged rather than be rewritten",
        ),
        verifier="astchecks.check_service_layer",
    ),
    "STR-002": Contract(
        template_id="con.node.thin_route_handlers",
        requirement=(
            "Every route handler in this file delegates its work. After the "
            "change, no handler passed to a route registration may contain a "
            "database call, and no handler body may hold more than 8 statements. "
            "The logic moves to a controller or service module the handler calls."
        ),
        boundary=(
            "This route file, and the controller or service module the handlers "
            "delegate to, which may be created or extended. No other file."
        ),
        forbidden=(
            "the HTTP method and path literal of every route registration",
            "the order the routes are registered in",
            "the module export of the router itself",
            "any middleware registered in this file, including its position",
            "the response each route produces for the same input",
        ),
        verifier="astchecks.check_thin_routes",
    ),
    "STR-003": Contract(
        template_id="con.react.error_boundary",
        requirement=(
            "An error boundary wraps the root of the component tree. After the "
            "change, a class component defining componentDidCatch or "
            "getDerivedStateFromError exists in the project, and that component "
            "appears as a JSX element enclosing the application root in the file "
            "that calls createRoot or ReactDOM.render."
        ),
        boundary=(
            "The file that mounts the React root, and one error boundary component "
            "module it may create. No other file."
        ),
        forbidden=(
            "the root component that is rendered, which must still be rendered inside the boundary",
            "the DOM container id passed to createRoot or ReactDOM.render",
            "the props any existing component receives",
            "the exports of any existing component module",
            "any provider or router already wrapping the root, whose nesting order must hold",
        ),
        verifier="astchecks.check_boundary",
    ),
    "STR-004": Contract(
        template_id="con.react.catch_all_route",
        requirement=(
            "The client router handles unmatched routes. After the change, this "
            "file contains a Route whose path attribute is the literal * or /*, "
            "or a route object whose path property is one of those two values, "
            "and it renders a component for an unknown path."
        ),
        boundary=(
            "This router file only. The component it renders for an unknown path "
            "may be created in one new module beside it."
        ),
        forbidden=(
            "the path and element of every route already declared",
            "the position of the catch all, which must come after all other routes",
            "the export of the router component",
            "any nested route structure already present",
        ),
        verifier="astchecks.check_catch_all",
    ),
    "GIT-003": Contract(
        template_id="con.node.history_secret_removal",
        requirement=(
            "No credential appears in the committed history. After the change, "
            "scanning the history with git log finds no credential-shaped string "
            "on any added line, and the credential that was exposed has been "
            "rotated at its provider so the value in the old history is dead."
        ),
        boundary=(
            "The commits of this repository that introduced or carried the "
            "credential, and the working tree files that still reference it. "
            "Rotation happens at the provider, outside the repository."
        ),
        forbidden=(
            "rewriting a shared branch before every collaborator who has cloned it has agreed",
            "force pushing before the exposed credential has been rotated at its provider",
            "altering the content of any commit that never carried the credential",
            "changing what the working tree does, since only the stored value moves to the environment",
            "deleting history wholesale in place of removing the credential from it",
        ),
        verifier="entropy.scan_history",
    ),
    "GIT-007": Contract(
        template_id="con.react.history_secret_removal",
        requirement=(
            "No credential appears in the committed history. After the change, "
            "scanning the history with git log finds no credential-shaped string "
            "on any added line, and the credential that was exposed has been "
            "rotated at its provider so the value in the old history is dead."
        ),
        boundary=(
            "The commits of this repository that introduced or carried the "
            "credential, and the working tree files that still reference it. "
            "Rotation happens at the provider, outside the repository."
        ),
        forbidden=(
            "rewriting a shared branch before every collaborator who has cloned it has agreed",
            "force pushing before the exposed credential has been rotated at its provider",
            "altering the content of any commit that never carried the credential",
            "changing what the built bundle ships, since a client bundle cannot hold a secret at all",
            "deleting history wholesale in place of removing the credential from it",
        ),
        verifier="entropy.scan_history",
    ),
}


# --------------------------------------------------------------------------
# lookup and rendering
# --------------------------------------------------------------------------


def covers(rule_id: str) -> bool:
    """Whether this module owns a rule."""
    return rule_id in CONTRACTS


def get(rule_id: str) -> Contract:
    """The contract for a rule.

    Raises rather than returning None. A delegated issue reaching the loop with
    no contract is a gap, not a condition to pass over quietly.
    """
    found = CONTRACTS.get(rule_id)
    if found is None:
        raise ContractError(f"no delegated contract for {rule_id}")
    return found


def target(issue: RuleResult) -> str:
    """The file the agent is being pointed at.

    A history rule changes the repository rather than one file, and its findings
    carry a commit reference rather than a path, so the repository root is used
    and the boundary carries the real scope.
    """
    if issue.rule.check_type.value == "entropy_scan":
        return REPO
    where = issue.where[0] if issue.where else None
    if where is not None and where.file and not where.file.startswith("history:"):
        return where.file
    return REPO


def violation(issue: RuleResult) -> str:
    """What the check actually found, in its own words.

    Section 5.2 asks for what the rule check found, precisely. That is the
    audit's own detail plus the position it recorded, never a restatement of
    the rule, so the agent is told about this codebase rather than the rule
    book.
    """
    hits = issue.where
    if not hits:
        return issue.rule.description
    first = hits[0]
    where = first.file
    if first.line:
        where += f" line {first.line}"
    text = f"{first.detail} ({where})"
    if len(hits) > 1:
        others = ", ".join(h.file for h in hits[1:4])
        text += f". Also found in {others}"
        if len(hits) > 4:
            text += f" and {len(hits) - 4} more"
    return text


def render(issue: RuleResult) -> Delegation:
    """Build the Section 5.2 second form instruction for one delegated issue.

    Raises when the rule is not DYNAMIC-DELEGATED or has no contract, so a
    misrouted issue fails loudly rather than reaching an agent with the wrong
    kind of instruction.
    """
    rule = issue.rule
    if rule.fix_type is not FixType.DYNAMIC_DELEGATED:
        raise ContractError(
            f"{rule.rule_id} is {rule.fix_type.value}, not DYNAMIC-DELEGATED, so it "
            f"does not belong to this module"
        )
    contract = get(rule.rule_id)
    return Delegation(
        rule_id=rule.rule_id,
        file_path=target(issue),
        violation=violation(issue),
        requirement=contract.requirement,
        boundary=contract.boundary,
        forbidden="; ".join(contract.forbidden),
    )


# --------------------------------------------------------------------------
# the integration point module 3.5 will wire
# --------------------------------------------------------------------------

# 3.5 supplies this: send the delegation to the agent over MCP, let 3.6 re-run
# the rule checker, and report whether the rule actually passes now. The boolean
# is 3.6's verdict. Section 5.3 states agent self-report is never trusted, and
# this module has no way to check the agent's work itself, which is exactly why
# it does not decide the outcome.
Dispatch = Callable[[Delegation], bool]


def resolver(dispatch: Dispatch):
    """Build a resolve step for delegated issues, shaped for the 3.1 controller.

    Follows the pattern 3.2 and 3.3 established. This module's job ends at
    producing a correct instruction and handing it over. It never reports
    RESOLVED on its own reasoning, only on the verdict dispatch returns, since
    nothing here can confirm that an authored change did what was asked.
    """
    from prodpilot.loop import Outcome, Step

    def resolve(issue: RuleResult, attempt: int) -> Step:
        try:
            delegation = render(issue)
        except ContractError as exc:
            return Step(Outcome.BLOCKED, str(exc))
        try:
            passed = dispatch(delegation)
        except Exception as exc:
            logger.warning("dispatching %s raised: %s", issue.rule_id, exc)
            return Step(Outcome.UNRESOLVED, f"dispatching the constraint raised: {exc}")
        if passed:
            return Step(Outcome.RESOLVED, "the agent's change was verified against the rule")
        return Step(
            Outcome.UNRESOLVED,
            "the rule still fails after the agent's change",
        )

    return resolve


def delegated_rules():
    """Every DYNAMIC-DELEGATED rule in the frozen store, for coverage checks."""
    from prodpilot.rules import ALL_RULES

    return tuple(r for r in ALL_RULES if r.fix_type is FixType.DYNAMIC_DELEGATED)
