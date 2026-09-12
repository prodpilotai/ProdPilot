"""Feature vectors for the scoring model.

Scope is Phase 5 module 5.2. This turns one audited repository into a fixed
length vector of integers. It attaches no label, that is 5.3. It trains nothing,
that is 5.4. It is not wired into the gate, that is 5.5.

Every vector comes from a real audit run by audit.run over real files at the
commit module 5.1 pinned. Nothing here approximates or synthesises an audit.

Why 26 features
---------------
Section 6 of the Complete Solution Document gives the count and the shape and
nothing else: "each repository becomes a 25-feature binary and integer vector
derived from the audit checks (which rules pass, which fail, in which domains)".
No document lists the features individually, so the list is chosen here and the
reasoning recorded, the same way module 4.2 recorded its threshold.

The first 25 fall out of the ruleset rather than being aimed at:

    9   one assessed count per domain
    9   one failed count per domain
    6   one failed count per priority tier
    1   which stack the project is
    1   whether the project builds
    --
    26

There are 9 domains in rules.py and 6 priority tiers in blueprint.py, and there
are 2 stacks, which is one binary flag. That is 25 without padding and without
dropping anything, and each block earns its place.

The 26th is a deliberate departure from Section 6's count, made on evidence.
Trained on those 25, the model could not tell React projects that deploy from
ones that do not once their failing rules were cleared: both fell to the same
estimate, 0.138. What mostly decides whether a static site deploys is whether
its build succeeds, and no audit rule measures that, so no count derived from
the audit could carry it. built is 1 when Render's own build command succeeds
for the project and 0 when it fails, measured by module builds.

It is added rather than put in place of a weak feature. assessed_secrets
contributed nothing to the model, but it still means what it says, and reusing
its column would change the meaning of a position that existing rows, the
backup and every earlier evaluation were built on. So built goes at the end,
every earlier position keeps its meaning, and a row can only be widened from 25
to 26 by adding a real build result, never by guessing one.

Unlike the other 25, built is not read from the audit report. The caller
supplies it, from the recorded build results for the dataset and from a build
run at the time for a project the gate is asked about. A vector is refused
without it.

The two domain blocks are Section 6's sentence made countable: which rules
failed, in which domains, and how many were looked at in each. The priority
block is orthogonal to the domain block, because Section 4 grades severity by
priority and not by domain, and a P0 failure means something the domain counts
alone cannot express. The stack flag is needed because the same counts mean
different things across two rulesets of different sizes.

Why the audit score is not one of the features
-----------------------------------------------
It is carried on every row, but outside the vector, for 5.3 and 5.4 to use as a
baseline to compare the model against.

Keeping it out is deliberate. Module 2.4 computes that score as a fixed weighted
sum of exactly the rule outcomes this vector already encodes, so as a feature it
adds no information the model cannot already derive. Worse, it would hand the
model a shortcut to reproducing 2.4's formula, which is the circularity Section
6 warns about when it insists the labels come from real deployment outcomes
rather than from rule compliance.

How a domain that was not checked is represented
------------------------------------------------
By its assessed count being zero, which is a real integer and not a sentinel.

Module 1.2 established that an inapplicable requirement has to be visible as
inapplicable rather than folded in with the ones that passed. That matters here
because React declares connectivity and api not applicable and the ruleset holds
no rules for them at all, so a React project has nothing to say about those two
domains. A domain with 5 assessed and 0 failed is a domain that was checked and
was clean. A domain with 0 assessed is a domain the audit could not judge,
whether because the stack has no rules there or because every rule in it was
skipped at run time. Those are different rows of numbers, so a model can tell
them apart, which it could not do if both were simply zero failures.

Nothing is ever absent. scikit-learn requires every value to be numeric, finite
and present, so there is no None and no NaN anywhere in a vector.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from prodpilot import audit
from prodpilot.audit import Report
from prodpilot.blueprint import Domain, Priority, Stack
from prodpilot.detection import DetectionError

logger = logging.getLogger(__name__)

# Fixed order. scikit-learn matches features by position, so this order is the
# contract with modules 5.3 and 5.4 and must not be rearranged once training
# data exists.
DOMAINS = tuple(d.value for d in Domain)
PRIORITIES = tuple(p.value for p in Priority)

FEATURES: tuple[str, ...] = (
    tuple(f"assessed_{d}" for d in DOMAINS)
    + tuple(f"failed_{d}" for d in DOMAINS)
    + tuple(f"failed_{p.lower()}" for p in PRIORITIES)
    + ("is_node", "built")
)

SIZE = 26

# Where the matrix is written. Under the gitignored data directory, beside the
# manifest it is derived from.
DATA = Path("data")
MATRIX = DATA / "features.jsonl"
NAMES = DATA / "features.names.json"

REPO = "repo"
NEGATIVE = "negative"


class FeatureError(Exception):
    """Raised when a vector cannot be built correctly."""


@dataclass(frozen=True)
class Row:
    """One repository's features, traceable back to where it came from.

    score is the audit engine's own 0 to 100 number. It is carried for module
    5.3 and 5.4 and is deliberately not one of the 25, for the reason in the
    module docstring.

    rule_id names the rule a synthetic negative was built to violate, and is
    empty for a real repository. No label is attached here.
    """

    name: str
    kind: str
    stack: str
    commit: str
    values: tuple[int, ...]
    score: int
    rule_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "stack": self.stack,
            "commit": self.commit,
            "rule_id": self.rule_id,
            "score": self.score,
            "values": list(self.values),
        }


@dataclass(frozen=True)
class Skipped:
    """A repository that produced no vector, and why.

    Recorded rather than dropped. A dataset that quietly loses rows cannot be
    described honestly in a write up.
    """

    name: str
    kind: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "kind": self.kind, "reason": self.reason}


def vector(report: Report, built: int) -> tuple[int, ...]:
    """The 26 integers for one audit report and its build result.

    Raises when the report assessed nothing, since a project the audit could not
    judge has no features to describe it and must not become a row of zeros that
    looks like a clean project. Raises too when the build result is anything
    but 0 or 1, because an unknown build is not a failed one.
    """
    if not report.results:
        raise FeatureError("the audit assessed no rules, so there is nothing to describe")
    if built not in (0, 1) or isinstance(built, bool):
        raise FeatureError(f"the build result must be 0 or 1, not {built!r}")

    assessed = dict.fromkeys(DOMAINS, 0)
    failed = dict.fromkeys(DOMAINS, 0)
    by_priority = dict.fromkeys(PRIORITIES, 0)

    for result in report.results:
        if result.scored:
            assessed[result.domain.value] += 1
        if result.failed:
            failed[result.domain.value] += 1
            by_priority[result.priority.value] += 1

    values = (
        tuple(assessed[d] for d in DOMAINS)
        + tuple(failed[d] for d in DOMAINS)
        + tuple(by_priority[p] for p in PRIORITIES)
        + (1 if report.stack is Stack.NODE_EXPRESS else 0, built)
    )

    if len(values) != SIZE:
        raise FeatureError(f"built {len(values)} features, expected {SIZE}")
    for name, value in zip(FEATURES, values):
        if not isinstance(value, int) or isinstance(value, bool):
            raise FeatureError(f"{name} is {type(value).__name__}, not an integer")
    return values


def of(root: str | Path, name: str, kind: str, commit: str = "",
       rule_id: str = "", built: int | None = None) -> Row:
    """Audit one project and build its row.

    built is the project's recorded build result. Without one there is no row,
    for the reason vector gives.

    Raises FeatureError for anything that stops a vector being built, so the
    caller can record the reason rather than lose the repository silently. That
    includes a project the audit engine cannot process at all, which real
    repositories do contain: one in the collected corpus nests a syntax tree
    deeply enough to exhaust Python's recursion limit while the parser output is
    being read.
    """
    try:
        report = audit.run(root)
    except (NotADirectoryError, DetectionError, OSError) as exc:
        raise FeatureError(str(exc)) from exc
    except Exception as exc:
        # Deliberately broad, and only here. This walks hundreds of third party
        # repositories, and one of them containing something the audit engine
        # cannot process must cost that repository and not the whole run. The
        # exception type is kept in the reason so the accounting names what
        # happened rather than burying it as an unexplained exclusion.
        logger.warning("auditing %s raised %s: %s", name, type(exc).__name__, exc)
        raise FeatureError(f"the audit raised {type(exc).__name__}: {exc}") from exc

    if report.stack is Stack.UNRECOGNIZED:
        raise FeatureError(report.detection.reason or "no supported stack was detected")
    if built is None:
        raise FeatureError("no build result is recorded for this project")

    return Row(
        name=name,
        kind=kind,
        stack=report.stack.value,
        commit=commit,
        values=vector(report, built),
        score=report.score,
        rule_id=rule_id,
    )


Fetch = Callable[[object], Path]


def build(entries, negatives, fetch: Fetch,
          found: dict[tuple[str, str, str], int]) -> Iterator[Row | Skipped]:
    """Walk the whole dataset, yielding a row or a reason for every item.

    entries are module 5.1's manifest entries, fetched at their pinned commit
    through the function 5.1 already proved. negatives are its synthetic
    negatives, which are already on disk because they were built there. found
    holds each item's recorded build result by name, kind and rule_id.

    Every item yields exactly one result, so the counts always add up.
    """
    for entry in entries:
        try:
            root = fetch(entry)
        except Exception as exc:
            yield Skipped(entry.name, REPO, f"could not be fetched: {exc}")
            continue
        try:
            yield of(root, entry.name, REPO, commit=entry.commit,
                     built=found.get((entry.name, REPO, "")))
        except FeatureError as exc:
            yield Skipped(entry.name, REPO, str(exc))

    for negative in negatives:
        path = Path(negative["path"])
        try:
            yield of(path, negative["name"], NEGATIVE, rule_id=negative["rule_id"],
                     built=found.get((negative["name"], NEGATIVE, negative["rule_id"])))
        except FeatureError as exc:
            yield Skipped(negative["name"], NEGATIVE, str(exc))


def widen(rows: list[dict], found: dict[tuple[str, str, str], int]
          ) -> tuple[list[dict], list[Skipped]]:
    """Extend written 25 value rows to 26 with their recorded build results.

    How the matrix gained its 26th column without auditing anything again: the
    first 25 values are exactly what module 5.2 wrote, and the last is the
    build result recorded for the same name, kind and rule_id. A row with no
    recorded result is not widened with a guess. It is returned as skipped,
    with the reason, and never reaches the training set.
    """
    out: list[dict] = []
    skipped: list[Skipped] = []
    for row in rows:
        values = list(row["values"])
        if len(values) != SIZE - 1:
            raise FeatureError(
                f"{row['name']} has {len(values)} values, only a {SIZE - 1} value row "
                f"can be widened")
        key = (str(row["name"]), str(row["kind"]), str(row.get("rule_id") or ""))
        built = found.get(key)
        if built not in (0, 1):
            skipped.append(Skipped(key[0], key[1], "no recorded build result, so the "
                                                   "build feature could not be set"))
            continue
        out.append({**row, "values": values + [int(built)]})
    return out, skipped


def write(rows: list[Row], path: str | Path = MATRIX) -> None:
    """Write the matrix, one row per line, in the fixed feature order."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")


def write_names(path: str | Path = NAMES) -> None:
    """Write the feature names beside the matrix.

    So a consumer outside this codebase can read what each position means
    without importing anything.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"size": SIZE, "features": list(FEATURES)}, indent=2) + "\n",
        encoding="utf-8",
    )


def matrix(rows: list[Row]) -> list[list[int]]:
    """The rows as scikit-learn expects X: a list of equal length integer lists.

    Built here rather than by the caller so the column order is this module's
    responsibility and cannot drift.
    """
    return [list(row.values) for row in rows]
