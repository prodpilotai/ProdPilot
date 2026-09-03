"""Full re-audit after the bounded loop completes.

Scope is Phase 4 module 4.1. Section 3 of the Complete Solution Document places
this at Layer 3: a full re-audit once the loop has finished, whose score the
gate then reads. This module is the re-audit half only. It decides nothing.

It does not compare the new score against a threshold, does not decide whether
to loop again, and does not decide anything about deployment. Those are module
4.2. What it returns is a complete audit report and enough context for 4.2 to
act on it.

Why the whole audit runs again
------------------------------
Because the loop's own record of what it fixed is not a description of the
project. Three things make it drift:

The retry budget is spent across the whole run and an issue sent to manual
review is never attempted again, so a rule can be retired while its precondition
is still missing and then be satisfied minutes later by a rule that ran after
it. In a real run against the seeded project, the two rules covering .gitignore
and the container user were retired for exactly that reason, and both passed by
the time the run ended.

A fix can also break a rule that was passing before it. Nothing in the loop
looks for that, because module 3.6 re-runs only the rule being fixed.

And a rule that was never queued, because it passed the first audit, is never
looked at again during the run at all.

Only a complete audit of what is on disk answers all three, which is why this
runs the same fifty rule engine over the whole project rather than re-checking
the rules the loop happened to touch.

Reading nothing from the loop
-----------------------------
The report comes from audit.run and nothing else. The loop run is accepted only
so its stop reason travels with the result, and it is never consulted to decide
a rule's status. That keeps the same separation module 3.6 relies on: the thing
that changed the project has no influence on the thing that measures it.

Failing closed
--------------
The loop has been writing files, so the project can be in a worse state than it
started in. A fix that corrupted package.json leaves a project that no longer
detects as any supported stack, and audit.run answers that with an empty report
rather than an error. An empty report is not a passing report and must never be
read as one, so a re-audit that assessed no rules is reported as a failure with
its reason, and Reaudit.ok is False.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from prodpilot import audit
from prodpilot.audit import Report
from prodpilot.detection import DetectionError
from prodpilot.loop import LoopRun, Stop

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reaudit:
    """One full re-audit of a project after a loop run.

    report is the audit engine's own Report, unchanged and complete. It is the
    whole point of this module, and module 4.2 reads its score.

    stopped carries why the loop ended, for the record only. Nothing here reads
    it, and no rule status depends on it.

    reason is filled only when no usable report could be produced.
    """

    project: str
    report: Report | None
    stopped: Stop | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        """Whether this re-audit can be gated on.

        A report that assessed nothing is not a pass. Treating one as a pass
        would let a project that broke badly enough to stop detecting as a
        supported stack sail through the gate.
        """
        return self.report is not None and bool(self.report.results)

    @property
    def score(self) -> int | None:
        """The readiness score, or None when there is no usable report."""
        return self.report.score if self.ok else None

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "ok": self.ok,
            "reason": self.reason,
            "stopped": self.stopped.value if self.stopped else None,
            "report": self.report.to_dict() if self.report else None,
        }

    def summary(self) -> str:
        """One line for a terminal or a log."""
        if not self.ok:
            return f"{self.project}: re-audit produced no usable report, {self.reason}"
        return f"{self.project}: re-audit, {self.report.summary()}"


def after(root: str | Path, run: LoopRun | None = None) -> Reaudit:
    """Run the full audit again over the project as it now stands on disk.

    root is the project the loop just worked on. run is that loop run, accepted
    so its stop reason travels with the result; it is not read to decide
    anything about a rule.

    Never raises for a project that cannot be audited. The gate has to be able
    to refuse on the result rather than crash on it, so an unreadable or
    undetectable project comes back with ok False and a reason.
    """
    base = Path(root)
    stopped = run.stopped if run is not None else None
    name = base.name or str(base)

    try:
        report = audit.run(base)
    except (NotADirectoryError, DetectionError, OSError) as exc:
        logger.warning("re-audit of %s failed: %s", base, exc)
        return Reaudit(project=name, report=None, stopped=stopped, reason=str(exc))

    if not report.results:
        reason = report.detection.reason or "no rules applied to this project"
        logger.warning("re-audit of %s assessed no rules: %s", base, reason)
        return Reaudit(project=report.project, report=report, stopped=stopped, reason=reason)

    result = Reaudit(project=report.project, report=report, stopped=stopped)
    logger.info("%s", result.summary())
    return result
