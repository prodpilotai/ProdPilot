"""Priority queue and loop controller for the bounded agentic loop.

Scope is Phase 3 module 3.1. This module owns sequencing and bounds only: which
issue is next, how many times it may be attempted, when to stop, and what to
report when an issue cannot be resolved.

It produces no fixes. Classification and fix production are modules 3.2 through
3.4, the MCP instruction wiring is 3.5, and independent re-verification is 3.6.
Those arrive behind the two injected callables described below.

Section 5.3 of the Complete Solution Document sets the mechanics:

    Audit output becomes a priority-ordered queue, P0 first, one issue at a
    time. Pass moves to the next issue. Fail retries, up to 3 attempts per
    issue. A budget exhausted flags the issue for manual review with location
    and reason, and the loop continues. A hard ceiling of 5 full loop
    iterations applies across the whole run.

What an attempt is
------------------
One pass of the resolve step over one issue. The budget is 3 attempts per
issue, counted across the whole run rather than reset each cycle. Once an issue
is sent to manual review it is never attempted again, even if a later re-audit
raises it. That is the strictest reading of "3 per issue" and it means the run
terminates on the retry budget alone, before the iteration ceiling is even
consulted.

What an iteration is
--------------------
One full drain of the queue, followed by the scoring gate's re-audit, which may
hand back a fresh queue. That matches Layer 3 in Section 3: a full re-audit runs
after the loop completes, and below threshold the loop triggers again up to the
ceiling. So an iteration is not one issue and not one attempt, it is one
complete pass plus the re-audit that may follow it. The ceiling of 5 bounds how
many times the queue may be repopulated.

Phase 4 owns that re-audit and does not exist yet, so it arrives here as the
requeue callable. When Phase 4 is built this interpretation should be confirmed
against it.

What the resolve step is
------------------------
Everything between taking an issue off the queue and knowing whether it is
actually fixed: classification, fix production, the MCP tool call, the agent's
edit, and the independent re-check. From this controller's side that collapses
to one question, does the rule pass now.

The answer must already be the verified one. Section 5.3 states that agent
self-report is never trusted, so the outcome this controller acts on is the
result of module 3.6 re-running the rule checker, not anything the agent
claimed. This module cannot enforce that on its own, and the module that
supplies the callable is responsible for it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from prodpilot.audit import RuleResult
from prodpilot.blueprint import Priority

logger = logging.getLogger(__name__)

RETRIES = 3
CEILING = 5


class Outcome(str, Enum):
    """What one attempt achieved."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    BLOCKED = "blocked"


class Stop(str, Enum):
    """Why the run ended."""

    CLEARED = "cleared"
    CEILING = "ceiling"
    STALLED = "stalled"


class LoopError(Exception):
    """Raised when the controller is configured with impossible bounds."""


@dataclass(frozen=True)
class Step:
    """What the resolve step reports back for one attempt.

    RESOLVED means the rule checker confirms the rule now passes. UNRESOLVED
    means it still fails and another attempt is allowed. BLOCKED means the
    issue cannot be attempted at all, so retrying is pointless and the
    remaining budget is skipped.
    """

    outcome: Outcome
    detail: str = ""


@dataclass(frozen=True)
class Attempt:
    """One recorded attempt on one issue."""

    rule_id: str
    number: int
    outcome: Outcome
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "number": self.number,
            "outcome": self.outcome.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Review:
    """An issue the loop could not resolve, with location and reason.

    Section 5.3 requires an exhausted issue to be flagged for manual review
    with its location and reason, so this is a structured record the caller can
    render or store, not a log line.
    """

    rule_id: str
    priority: str
    domain: str
    fix_type: str
    requirement: str
    reason: str
    attempts: int
    locations: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "priority": self.priority,
            "domain": self.domain,
            "fix_type": self.fix_type,
            "requirement": self.requirement,
            "reason": self.reason,
            "attempts": self.attempts,
            "locations": list(self.locations),
        }


@dataclass(frozen=True)
class Cycle:
    """One full pass over the queue."""

    number: int
    queued: int
    attempted: int
    resolved: tuple[str, ...] = field(default_factory=tuple)
    exhausted: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "queued": self.queued,
            "attempted": self.attempted,
            "resolved": list(self.resolved),
            "exhausted": list(self.exhausted),
        }


@dataclass(frozen=True)
class LoopRun:
    """Everything one run of the loop did."""

    cycles: tuple[Cycle, ...]
    attempts: tuple[Attempt, ...]
    resolved: tuple[str, ...]
    review: tuple[Review, ...]
    stopped: Stop

    @property
    def iterations(self) -> int:
        return len(self.cycles)

    @property
    def cleared(self) -> bool:
        """Whether the loop resolved everything it was given."""
        return self.stopped is Stop.CLEARED and not self.review

    def to_dict(self) -> dict[str, object]:
        return {
            "iterations": self.iterations,
            "stopped": self.stopped.value,
            "resolved": list(self.resolved),
            "manual_review": [r.to_dict() for r in self.review],
            "cycles": [c.to_dict() for c in self.cycles],
            "attempts": [a.to_dict() for a in self.attempts],
        }

    def summary(self) -> str:
        return (
            f"{self.iterations} iteration(s), {len(self.resolved)} resolved, "
            f"{len(self.review)} for manual review, stopped: {self.stopped.value}"
        )


def order(issues: Sequence[RuleResult]) -> tuple[RuleResult, ...]:
    """Put issues in the order the loop works through them, P0 first.

    The audit report already sorts its issues this way. Sorting again here
    means the controller is correct for any caller, not only that one, and it
    keeps the ordering rule in the module that depends on it.
    """
    rank = {p: i for i, p in enumerate(Priority)}
    return tuple(sorted(issues, key=lambda r: (rank[r.priority], r.rule_id)))


Resolve = Callable[[RuleResult, int], Step]
Requeue = Callable[[Cycle], Sequence[RuleResult]]


class Loop:
    """The bounded controller.

    resolve is called once per attempt with the issue and the attempt number,
    which lets a later module tighten a constraint on a retry.

    requeue is called after each cycle with that cycle's record and returns the
    issues still outstanding. Phase 4's scoring gate supplies it. Left as None,
    the run is a single cycle.
    """

    def __init__(
        self,
        resolve: Resolve,
        requeue: Requeue | None = None,
        retries: int = RETRIES,
        ceiling: int = CEILING,
    ) -> None:
        if retries < 1:
            raise LoopError(f"retries must be at least 1, got {retries}")
        if ceiling < 1:
            raise LoopError(f"ceiling must be at least 1, got {ceiling}")
        self.resolve = resolve
        self.requeue = requeue
        self.retries = retries
        self.ceiling = ceiling

    def run(self, issues: Sequence[RuleResult]) -> LoopRun:
        """Work the queue until it is clear or a bound stops the run."""
        cycles: list[Cycle] = []
        attempts: list[Attempt] = []
        resolved: list[str] = []
        review: list[Review] = []
        spent: dict[str, int] = {}
        closed: set[str] = set()

        queue = order(issues)
        stopped = Stop.CLEARED

        for number in range(1, self.ceiling + 1):
            # Anything already resolved or already sent to manual review is
            # finished, whatever a later re-audit says about it.
            pending = [i for i in queue if i.rule_id not in closed]
            done_now: list[str] = []
            spent_now: list[str] = []
            tries = 0

            for issue in pending:
                outcome, detail, used = self._work(issue, spent, attempts)
                tries += used
                if outcome is Outcome.RESOLVED:
                    closed.add(issue.rule_id)
                    resolved.append(issue.rule_id)
                    done_now.append(issue.rule_id)
                    continue
                closed.add(issue.rule_id)
                spent_now.append(issue.rule_id)
                review.append(self._review(issue, outcome, detail, spent[issue.rule_id]))

            cycle = Cycle(
                number=number,
                queued=len(pending),
                attempted=tries,
                resolved=tuple(done_now),
                exhausted=tuple(spent_now),
            )
            cycles.append(cycle)
            logger.info(
                "cycle %s: %s queued, %s attempt(s), %s resolved, %s for review",
                number, len(pending), tries, len(done_now), len(spent_now),
            )

            if tries == 0:
                # Nothing in the queue could be acted on, so another cycle
                # would do the same nothing. Bounded already, but spinning to
                # the ceiling doing no work reads as a hang.
                stopped = Stop.STALLED if pending else Stop.CLEARED
                break

            if self.requeue is None:
                stopped = Stop.CLEARED
                break

            queue = order(self._ask(cycle))
            if not [i for i in queue if i.rule_id not in closed]:
                stopped = Stop.CLEARED
                break
        else:
            stopped = Stop.CEILING
            logger.info("stopped at the ceiling of %s iteration(s)", self.ceiling)

        return LoopRun(
            cycles=tuple(cycles),
            attempts=tuple(attempts),
            resolved=tuple(resolved),
            review=tuple(review),
            stopped=stopped,
        )

    def _work(
        self,
        issue: RuleResult,
        spent: dict[str, int],
        attempts: list[Attempt],
    ) -> tuple[Outcome, str, int]:
        """Attempt one issue until it resolves or its budget runs out."""
        used = 0
        outcome = Outcome.UNRESOLVED
        detail = ""

        while spent.get(issue.rule_id, 0) < self.retries:
            number = spent.get(issue.rule_id, 0) + 1
            spent[issue.rule_id] = number
            used += 1
            step = self._step(issue, number)
            outcome, detail = step.outcome, step.detail
            attempts.append(Attempt(issue.rule_id, number, outcome, detail))
            if outcome is Outcome.RESOLVED:
                return outcome, detail, used
            if outcome is Outcome.BLOCKED:
                return outcome, detail, used
        return outcome, detail, used

    def _step(self, issue: RuleResult, number: int) -> Step:
        """Call the resolve step, turning a raised error into a failed attempt.

        A resolve step that raises is a defect somewhere below this controller,
        and letting it escape would abandon every remaining issue. It is
        recorded as unresolved so the retry budget absorbs it, since a later
        module may fail transiently on a subprocess or a timeout.
        """
        try:
            step = self.resolve(issue, number)
        except Exception as exc:
            logger.warning("resolve step raised on %s: %s", issue.rule_id, exc)
            return Step(Outcome.UNRESOLVED, f"resolve step raised: {exc}")
        if not isinstance(step, Step):
            raise LoopError(
                f"resolve step returned {type(step).__name__}, expected Step"
            )
        return step

    def _ask(self, cycle: Cycle) -> Sequence[RuleResult]:
        """Ask for the next queue, treating a raised error as an empty one."""
        try:
            return self.requeue(cycle) or ()
        except Exception as exc:
            logger.warning("requeue raised after cycle %s: %s", cycle.number, exc)
            return ()

    def _review(
        self, issue: RuleResult, outcome: Outcome, detail: str, tries: int
    ) -> Review:
        """Build the manual review record for an issue the loop gave up on."""
        if outcome is Outcome.BLOCKED:
            reason = detail or "the issue could not be attempted"
        else:
            reason = (
                f"unresolved after {tries} attempt(s)"
                + (f": {detail}" if detail else "")
            )
        return Review(
            rule_id=issue.rule_id,
            priority=issue.priority.value,
            domain=issue.domain.value,
            fix_type=issue.fix_type.value,
            requirement=issue.rule.description,
            reason=reason,
            attempts=tries,
            locations=tuple(f.to_dict() for f in issue.where),
        )
