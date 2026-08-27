"""Score computation and reporting for the audit engine.

Scope is Phase 2 module 2.4. This aggregates the three check families into one
report, computes a readiness score, and puts the failures in the order the
Phase 3 loop will work through them. It fixes nothing and it deploys nothing.

Which score this is
-------------------
Section 6 of the Complete Solution Document describes the readiness score as
the calibrated probability output of a GradientBoostingClassifier. That model is
Phase 5 and does not exist yet.

What this module produces is the earlier of the two scores, the one Section 3 of
the Phased Implementation Plan asks module 2.4 for and the one Phase 4 module
4.2 gates on provisionally: a deterministic score computed straight from rule
results. No model is involved, and none is stubbed. When Phase 5 lands, module
4.3 swaps the source of the number while the bands and the report stay as they
are.

The formula
-----------
No canonical document specifies how to turn rule results into a number, so the
choice is made here and recorded rather than left implicit.

One verdict per rule, not per finding. A file scoped rule reports once per file,
so a project with four route files would otherwise weigh that rule four times.
A rule fails if any file fails it, which is also the only reading that matches
what the rule says.

Weighted by priority, halving at each tier: P0 is 32, P1 is 16, P2 is 8, P3 is
4, P4 is 2, P5 is 1. Section 4 orders the tiers but gives no weights. Halving
keeps the ordering the document states and makes one P0 worth more than every
P5 in the ruleset combined, which matches P0 meaning critical.

    score = 100 * (weight of rules passed) / (weight of rules assessed)

Rules that do not apply to the project are excluded from both sides, since
scoring a project against a rule for the other stack would be meaningless. A
rule whose source could not be parsed counts against the score rather than being
excluded, because excluding it would make an unreadable file the cheapest way to
raise a score.

No blocker cap is applied. A project that fails one P0 and passes everything
else still scores in the nineties under a purely weighted formula, which
arguably overstates it, but capping is deployment policy and Phase 4 owns the
gate. The report surfaces the failed P0 rules separately so that gate can apply
whatever policy it decides on without this module inventing one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from prodpilot import astchecks, filechecks
from prodpilot.blueprint import Domain, Priority, Stack
from prodpilot.detection import DetectionResult, detect_stack
from prodpilot.findings import Finding, Status
from prodpilot.rules import FixType, Rule, Scope, get_rule, rules_for_stack

logger = logging.getLogger(__name__)

# Priority weights, halving at each tier. See the module docstring.
WEIGHTS: dict[Priority, int] = {
    Priority.P0: 32,
    Priority.P1: 16,
    Priority.P2: 8,
    Priority.P3: 4,
    Priority.P4: 2,
    Priority.P5: 1,
}


class Band(str, Enum):
    """The four readiness bands named in Section 6."""

    NOT_READY = "Not Ready"
    NEEDS_WORK = "Needs Work"
    NEARLY_READY = "Nearly Ready"
    PRODUCTION_READY = "Production Ready"


# Lower bound of each band, per Section 6.
BANDS: tuple[tuple[int, Band], ...] = (
    (90, Band.PRODUCTION_READY),
    (70, Band.NEARLY_READY),
    (40, Band.NEEDS_WORK),
    (0, Band.NOT_READY),
)


def band_of(score: int) -> Band:
    """The band a score falls in."""
    for floor, band in BANDS:
        if score >= floor:
            return band
    return Band.NOT_READY


@dataclass(frozen=True)
class RuleResult:
    """One rule's verdict for the whole project.

    Carries what the Phase 3 loop needs to act: which rule, how it failed, where
    to look, and how the fix will be produced.
    """

    rule: Rule
    status: Status
    evidence: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def rule_id(self) -> str:
        return self.rule.rule_id

    @property
    def priority(self) -> Priority:
        return self.rule.priority

    @property
    def domain(self) -> Domain:
        return self.rule.domain

    @property
    def fix_type(self) -> FixType:
        return self.rule.fix_type

    @property
    def scope(self) -> Scope:
        return self.rule.scope

    @property
    def weight(self) -> int:
        return WEIGHTS[self.priority]

    @property
    def scored(self) -> bool:
        """Whether this rule counts towards the score at all."""
        return self.status is not Status.SKIPPED

    @property
    def failed(self) -> bool:
        return self.status in (Status.FAIL, Status.UNPARSED)

    @property
    def where(self) -> tuple[Finding, ...]:
        """The findings that decided this verdict."""
        return tuple(f for f in self.evidence if f.status is self.status)

    @property
    def detail(self) -> str:
        hits = self.where
        return hits[0].detail if hits else self.rule.description

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "priority": self.priority.value,
            "domain": self.domain.value,
            "scope": self.scope.value,
            "check_type": self.rule.check_type.value,
            "fix_type": self.fix_type.value,
            "requirement": self.rule.description,
            "detail": self.detail,
            "weight": self.weight,
            "locations": [f.to_dict() for f in self.where],
        }


# A rule's verdict is the worst outcome any file produced for it.
_SEVERITY = {Status.FAIL: 3, Status.UNPARSED: 2, Status.PASS: 1, Status.SKIPPED: 0}


def verdict(findings: list[Finding]) -> Status:
    """Collapse one rule's findings into a single outcome.

    A rule fails if any file fails it. That is both the safe reading and the
    only one that matches what a rule states, since a rule is a property of the
    project rather than of one file.
    """
    if not findings:
        return Status.SKIPPED
    return max((f.status for f in findings), key=lambda s: _SEVERITY[s])


def collect(findings: list[Finding], stack: Stack) -> list[RuleResult]:
    """Group findings by rule and resolve each to one verdict.

    Every rule for the stack appears in the result, including ones no check
    produced a finding for, so a silently missing check shows as SKIPPED rather
    than vanishing from the report.
    """
    by_rule: dict[str, list[Finding]] = {}
    for f in findings:
        by_rule.setdefault(f.rule_id, []).append(f)

    out: list[RuleResult] = []
    for rule in rules_for_stack(stack):
        hits = by_rule.pop(rule.rule_id, [])
        out.append(RuleResult(rule=rule, status=verdict(hits), evidence=tuple(hits)))

    for rule_id, hits in by_rule.items():
        rule = get_rule(rule_id)
        if rule is None:
            raise ValueError(f"finding references an unknown rule: {rule_id}")
        logger.info("%s produced findings but does not belong to %s", rule_id, stack.value)
    return out


def score_of(results: list[RuleResult]) -> int:
    """Readiness score from 0 to 100, weighted by priority."""
    scored = [r for r in results if r.scored]
    available = sum(r.weight for r in scored)
    if not available:
        return 0
    earned = sum(r.weight for r in scored if r.status is Status.PASS)
    return round(100 * earned / available)


@dataclass(frozen=True)
class Report:
    """The full audit of one project."""

    project: str
    stack: Stack
    detection: DetectionResult
    score: int
    band: Band
    results: tuple[RuleResult, ...]

    @property
    def issues(self) -> tuple[RuleResult, ...]:
        """Failed rules, worst priority first.

        This is the queue Phase 3 consumes. Section 5.3 processes one issue at
        a time in priority order, so the ordering is settled here rather than
        at the call site.
        """
        order = {p: i for i, p in enumerate(Priority)}
        failed = [r for r in self.results if r.failed]
        return tuple(sorted(failed, key=lambda r: (order[r.priority], r.rule_id)))

    @property
    def blockers(self) -> tuple[RuleResult, ...]:
        """Failed P0 rules.

        Surfaced separately so the Phase 4 gate can treat a critical failure as
        a blocker without this module deciding that policy.
        """
        return tuple(r for r in self.issues if r.priority is Priority.P0)

    @property
    def passed(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.status is Status.PASS)

    @property
    def skipped(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.status is Status.SKIPPED)

    def by_priority(self) -> dict[str, int]:
        """Count of failures per priority tier."""
        out = {p.value: 0 for p in Priority}
        for r in self.issues:
            out[r.priority.value] += 1
        return out

    def by_domain(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.issues:
            out[r.domain.value] = out.get(r.domain.value, 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict[str, object]:
        """The structured report the Phase 3 loop consumes."""
        return {
            "project": self.project,
            "stack": self.stack.value,
            "detection": self.detection.to_dict(),
            "score": self.score,
            "band": self.band.value,
            "counts": {
                "assessed": len([r for r in self.results if r.scored]),
                "passed": len(self.passed),
                "failed": len(self.issues),
                "skipped": len(self.skipped),
                "blockers": len(self.blockers),
            },
            "failures_by_priority": self.by_priority(),
            "failures_by_domain": self.by_domain(),
            "issues": [r.to_dict() for r in self.issues],
            "passed_rules": [r.rule_id for r in self.passed],
            "skipped_rules": [r.rule_id for r in self.skipped],
        }

    def summary(self) -> str:
        """One line for a terminal or a log."""
        return (
            f"{self.project}: {self.score}/100 {self.band.value}, "
            f"{len(self.issues)} issue(s), {len(self.blockers)} blocker(s)"
        )


def run(root: str | Path) -> Report:
    """Audit a project and return its report.

    Raises NotADirectoryError when the path is not a project directory, and
    DetectionError when the path cannot be inspected at all.
    """
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"not a project directory: {base}")

    found = detect_stack(base)
    if not found.is_supported:
        logger.info("no audit for %s: %s", base, found.reason)
        return Report(
            project=base.name,
            stack=found.stack,
            detection=found,
            score=0,
            band=Band.NOT_READY,
            results=(),
        )

    findings: list[Finding] = []
    findings.extend(astchecks.check_project(base))
    findings.extend(filechecks.check_project(base, stack=found.stack))

    results = collect(findings, found.stack)
    score = score_of(results)
    report = Report(
        project=base.name,
        stack=found.stack,
        detection=found,
        score=score,
        band=band_of(score),
        results=tuple(results),
    )
    logger.info("%s", report.summary())
    return report
