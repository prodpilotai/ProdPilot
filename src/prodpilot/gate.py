"""The scoring gate: decide whether a project may deploy, and loop if not.

Scope is Phase 4, modules 4.2 and 4.3. Module 4.2 gates on the audit engine's
0 to 100 score with a threshold and a blocker rule. Module 4.3 adds the model
Phase 5 trained on real Render outcomes, as a third condition beside that score
rather than in place of it. Why it sits beside the score and not in its place
is set out in reading, because that is where the Implementation Phases document
expected the change to land.

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

from prodpilot import audit, builds, reaudit, scoring
from prodpilot.builds import BuildError
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
    chance: float | None = None
    operating: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "ready": self.ready,
            "reason": self.reason,
            "threshold": self.threshold,
            "score": self.score,
            "band": self.band,
            "deploy_chance": round(self.chance, 4) if self.chance is not None else None,
            "operating_point": (round(self.operating, 4)
                                if self.operating is not None else None),
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
    """The score the gate compares against its threshold, and the band's source.

    The audit engine's own 0 to 100 score, as module 4.2 built it.

    The Implementation Phases document expected module 4.3 to replace this with
    the calibrated model's probability and leave the threshold and the bands
    unchanged. Measured on the real dataset, that cannot work. The model is
    honest: its probabilities match observed deploy rates. And no project in
    675 real projects reaches a calibrated 0.9, not even with every failing rule
    set to passing: on the model trained with the build result, the highest
    estimate is 0.802 as audited and 0.887 fully fixed. Much of what makes a
    deployment fail, a database it needs or a secret it lacks, is outside
    anything the audit measures, so the model cannot be that sure. A gate at 90
    on the probability would never open, and ProdPilot would never deploy.

    The model trained before the build result existed was not fit to gate React:
    with every failing rule cleared, React projects that deployed and ones that
    did not fell to the same estimate. The model trained with it is, and the
    evidence is recorded beside VETO, where the decision is set.

    So the audit score keeps its place, with its threshold of 90, its blocker
    rule and its bands, and the model is asked a separate question in estimate:
    is the chance this project really deploys at or above the operating point
    module 5.4 chose. That is a deliberate deviation from the document's wording,
    recorded here and in the commit that made it.
    """
    return result.score


# Stacks whose deployments the model's estimate may block. For a stack not named
# here the estimate is still produced and reported beside the score, but it does
# not decide; the audit half of the gate still does.
#
# Both stacks are named. The decision was taken from module 5.4's evaluation,
# docs/evaluation.md, against a rule fixed before its numbers existed: React is
# blocked by the model only if, with every failing rule cleared, the estimate
# separates React projects that deployed from ones that did not with a ROC AUC
# of at least 0.70 on all rows and on the held out rows, the held out ROC AUC
# within React is at least 0.70, and at least three quarters of fully fixed
# React deployers clear the operating point. On the model trained with the
# build result and the monotonic constraint those are 0.864, 0.830, 0.852 and
# 108 of 109. On the model before it they were 0.489, 0.514, 0.653 and 7 of 109,
# and React would have stayed advisory. For Express, where the same measures
# are 0.858, 0.797, 0.913 and 20 of 20, the model already decided.
VETO = frozenset({"node_express", "react_vite"})


def estimate(result: Reaudit) -> tuple[float | None, float | None]:
    """The model's chance this project really deploys, and its operating point.

    Module 4.3's half of the gate. Both numbers come from module 5.5, and the
    operating point is the threshold module 5.4 chose on its training rows.

    A model that cannot be loaded gives no estimate rather than a guess, and no
    estimate keeps the gate shut. The audit score is never used in its place,
    because the two numbers answer different questions.

    The model also needs to know whether the project builds, which the audit
    does not say. That comes from module builds, which runs Render's own build
    command on the audited directory and holds the result until the project's
    files change, so a run of several cycles builds once. A build that could
    not be determined, or a check that could not run, gives no estimate, for
    the same reason a missing model does: an unknown build is not a failed one.
    """
    if result.report is None:
        return None, None
    if not result.root:
        logger.error("no deployability estimate for %s: the re-audit does not say "
                     "where the project is, so it cannot be built", result.project)
        return None, None
    try:
        found = builds.cached(result.root, result.report.stack.value)
    except BuildError as exc:
        logger.error("no deployability estimate for %s: the build check could not "
                     "run: %s", result.project, exc)
        return None, None
    if found.built is None:
        logger.error("no deployability estimate for %s: its build was undetermined, "
                     "%s", result.project, found.reason)
        return None, None
    try:
        return scoring.estimate(result.report, found.built)
    except (ScoreError, FeatureError) as exc:
        logger.error("no deployability estimate for %s: %s", result.project, exc)
        return None, None


def clears(result: Reaudit, threshold: int = THRESHOLD) -> tuple[bool, str]:
    """Whether a re-audit opens the gate, and the reason either way.

    Three conditions, all of which must hold, checked in this order: no critical
    rule still failing, the audit score at the threshold, and the model's
    estimate at its operating point. The deterministic checks come first, so a
    project the fix loop has not finished with never needs the model at all.

    Fails closed. A project that could not be audited, or that the model could
    not estimate, does not deploy.
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
    if score is None or score < threshold:
        return False, f"score {score} is below the threshold of {threshold}"

    chance, operating = estimate(result)
    stack = result.report.stack.value
    if stack not in VETO:
        said = (f"the model estimates a {chance:.0%} chance of deploying"
                if chance is not None else "no model estimate could be produced")
        return True, (f"score {score} meets the threshold of {threshold} with no critical "
                      f"failures; {said}, which is advisory for {stack}")
    if chance is None or operating is None:
        return False, (f"score {score} meets the threshold, but no deployability "
                       f"estimate could be produced, so the gate stays shut. See "
                       f"the log for why")
    if chance < operating:
        return False, (f"score {score} meets the threshold, but the model estimates "
                       f"a {chance:.0%} chance of deploying, below its operating "
                       f"point of {operating:.0%}")
    return True, (f"score {score} meets the threshold of {threshold} with no critical "
                  f"failures, and the model estimates a {chance:.0%} chance of "
                  f"deploying, at or above its operating point of {operating:.0%}")


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
    # report, so the score and the band always describe the same number. The
    # model's estimate travels beside them, so a developer sees both numbers.
    score = reading(final)
    chance, operating = estimate(final) if final.ok else (None, None)
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
        chance=chance,
        operating=operating,
    )
    logger.info("%s", decision.summary())
    return decision
