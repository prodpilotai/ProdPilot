"""The scoring gate: decide whether a project may deploy, and loop if not.

Scope is Phase 4 module 4.2, the provisional stage. It gates on the raw audit
engine score from Phase 2, because the calibrated probability from the ML model
does not exist yet. That is deliberate, not a shortcut: module 4.3 swaps the
score source once Phase 5 module 5.5 lands, and the Phased Implementation Plan
is explicit that the bands and the gate behaviour do not change when it does.

No model is referenced here, and none is stubbed.

Where the threshold comes from
------------------------------
Neither canonical document states a number. Section 3 of the Complete Solution
Document says only "score meets threshold: deployment proceeds", and Section 6
gives the bands: 0 to 39 Not Ready, 40 to 69 Needs Work, 70 to 89 Nearly Ready,
90 to 100 Production Ready.

So the number is a judgment call, recorded here rather than left implicit. It is
90, the lower bound of the only band that claims the project is ready for
production. Deploying something the report itself labels Nearly Ready would
contradict the label, and no lower boundary describes a deployable state. Module
4.3 keeps this number and changes only where the score comes from.

Why a score alone does not open the gate
----------------------------------------
Module 2.4 left this decision here in as many words: it applies no blocker cap,
notes that a project failing one P0 and passing everything else still scores in
the nineties, and says capping is deployment policy that Phase 4 owns.

That is measurable rather than hypothetical. One failed P0 with everything else
passing scores 93 on the Node ruleset and 91 on React, both clear of 90. Since
P0 means critical, a gate on the score alone would deploy a project with a
critical failure still open. So the gate requires both: the score meets the
threshold, and no P0 rule is failing. The two conditions are reported
separately, so the policy can be changed without touching the score.

How looping again works
-----------------------
Section 3 describes the below-threshold path as the loop triggering again, up to
the ceiling. That mechanism already exists. Module 3.1's controller takes a
requeue callable, calls it after each cycle, and enforces the ceiling of five
itself. This module supplies that callable and nothing more: it re-audits
through module 4.1, and hands back the issues the project still has, or nothing
at all once the gate is satisfied. The ceiling, the retry budget and the manual
review records all stay exactly where 3.1 put them.

The final score is measured once more after the loop ends rather than reused
from the last requeue, because the decision that reaches a developer should come
from a reading taken after the last change, not before it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot import audit, reaudit, scoring
from prodpilot.audit import RuleResult, band_of
from prodpilot.detection import DetectionError
from prodpilot.features import FeatureError
from prodpilot.loop import CEILING, Loop, Resolve, Review
from prodpilot.reaudit import Reaudit
from prodpilot.scoring import ScoreError

logger = logging.getLogger(__name__)

# The lower bound of the Production Ready band. See the module docstring for why
# this number and not another.
THRESHOLD = 90


@dataclass(frozen=True)
class Decision:
    """What the gate decided, and everything a caller needs to act on it."""

    project: str
    ready: bool
    reason: str
    threshold: int
    score: int | None = None
    band: str | None = None
    cycles: int = 0
    stopped: str = ""
    resolved: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    review: tuple[Review, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "ready": self.ready,
            "reason": self.reason,
            "threshold": self.threshold,
            "score": self.score,
            "band": self.band,
            "cycles": self.cycles,
            "stopped": self.stopped,
            "resolved": list(self.resolved),
            "blockers": list(self.blockers),
            "manual_review": [r.to_dict() for r in self.review],
        }

    def summary(self) -> str:
        state = "ready to deploy" if self.ready else "not ready"
        return (
            f"{self.project}: {state}, {self.reason}. "
            f"{self.cycles} cycle(s), {len(self.resolved)} rule(s) fixed, "
            f"{len(self.review)} for manual review"
        )


def reading(result: Reaudit) -> int | None:
    """The score the gate reads.

    The one place the source of the number lives, so module 4.3 is a change here
    and nowhere else. The threshold comparison, the band and the reason text are
    all computed from whatever this returns.

    Module 4.3, done. This returns the trained GradientBoostingClassifier's
    calibrated probability for the project, mapped onto the same 0 to 100 range,
    through module 5.5. It no longer returns Reaudit.score, the audit engine's
    own priority weighted number.

    Nothing else moved. The threshold is still 90, the blocker rule still
    stands, and the band is still derived from this return value rather than
    from the report, so it followed the new source without being touched.

    A model that cannot be loaded produces no score rather than falling back to
    the audit engine's. The two numbers mean different things, so reporting one
    as the other would be a lie a reader could not detect. No score means the
    gate stays shut, which is the behaviour module 4.2 already built.
    """
    if result.report is None:
        return None
    try:
        return scoring.score(result.report)
    except (ScoreError, FeatureError) as exc:
        logger.error("no calibrated score for %s: %s", result.project, exc)
        return None


def clears(result: Reaudit, threshold: int = THRESHOLD) -> tuple[bool, str]:
    """Whether a re-audit opens the gate, and the reason either way.

    Fails closed. A project that could not be audited does not deploy, because
    an absent score is not a passing score.
    """
    if not result.ok or result.report is None:
        return False, result.reason or "the project could not be audited"

    score = reading(result)
    blockers = [r.rule_id for r in result.report.blockers]

    if blockers:
        return False, (
            f"score {score} but {len(blockers)} critical rule(s) still fail: "
            f"{', '.join(blockers)}"
        )
    if score is None:
        # Module 5.5 could not produce a calibrated score. The gate stays shut
        # rather than reaching for the audit engine's number, which measures
        # something else.
        return False, ("no calibrated score could be produced, so the gate "
                       "stays shut. See the log for why")
    if score < threshold:
        return False, f"score {score} is below the threshold of {threshold}"
    return True, f"score {score} meets the threshold of {threshold} with no critical failures"


def run(
    root: str | Path,
    resolve: Resolve,
    threshold: int = THRESHOLD,
    ceiling: int = CEILING,
) -> Decision:
    """Audit, fix, re-audit, and decide, looping while the gate stays shut.

    resolve is module 3.1's resolve step, normally dispatch.Fixer.resolve with a
    real verifier behind it. This module never touches a fix itself.

    Never raises for a project that cannot be read. The caller has to be able to
    refuse on a decision rather than crash on an exception, so an unreadable or
    undetectable project comes back as a decision that is not ready.
    """
    base = Path(root)
    name = base.name or str(base)

    try:
        first = audit.run(base)
    except (NotADirectoryError, DetectionError, OSError) as exc:
        logger.warning("gate cannot audit %s: %s", base, exc)
        return Decision(project=name, ready=False, reason=str(exc), threshold=threshold)

    def requeue(cycle) -> Sequence[RuleResult]:
        """What module 3.1 asks for after each cycle: the issues that remain.

        Returning nothing stops the loop, which is how the gate being satisfied
        ends the run. The ceiling is 3.1's and is not consulted here.
        """
        result = reaudit.after(base)
        passed, why = clears(result, threshold)
        logger.info("after cycle %s: %s", cycle.number, why)
        if passed or not result.ok:
            return ()
        return result.report.issues

    done = Loop(resolve, requeue, ceiling=ceiling).run(first.issues)
    final = reaudit.after(base, done)
    passed, why = clears(final, threshold)

    # The band is computed from the gate's own reading rather than taken off the
    # report, so the score and the band always describe the same number. Taking
    # it off the report would leave a second score source behind, and module 4.3
    # would then report a calibrated score beside the audit score's band.
    score = reading(final)
    decision = Decision(
        project=final.project or name,
        ready=passed,
        reason=why,
        threshold=threshold,
        score=score,
        band=band_of(score).value if score is not None else None,
        cycles=done.iterations,
        stopped=done.stopped.value,
        resolved=done.resolved,
        blockers=tuple(r.rule_id for r in final.report.blockers) if final.ok else (),
        review=done.review,
    )
    logger.info("%s", decision.summary())
    return decision
