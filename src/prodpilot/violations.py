"""How the violations found in real repositories split by fix type.

RQ2 asks what share of the production readiness violations in real Node.js and
React repositories can be fixed without model-written content. The frozen
ruleset answers it at design time, 44 of 50 rules. This module answers it over
what real projects actually fail.

The population is every repository module 5.1 kept, read from its cloned copy
in data/cache at the commit recorded in the manifest. The synthetic negatives
are left out: each was made by breaking a rule on purpose, so counting them
would count the experiment rather than the world.

For every repository the frozen audit runs unchanged, and every failing rule
instance is recorded with its rule, domain, priority and fix type. For every
failing DYNAMIC-PARAMETRIC instance the contract is also rendered, which runs
the extraction and nothing else, so the real-world refusal rate can be set
beside the rate measured on samples built to be ambiguous. Rendering a
contract reads the project and writes nothing.

The run is resumable. Each repository's record is appended to a JSON lines
file as soon as it is done, and a rerun skips the ones already there, so a
stopped run continues rather than restarting.

    python -m prodpilot.violations data/revision/violations.jsonl docs/revision
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from prodpilot import audit, dispatch, extraction
from prodpilot.extraction import ExtractError
from prodpilot.rules import get_rule

logger = logging.getLogger(__name__)

CACHE = Path("data") / "cache"
TYPES = ("STATIC", "DYNAMIC-PARAMETRIC", "DYNAMIC-DELEGATED")
STACKS = ("node_express", "react_vite")


# Why extraction declined, in four kinds. A precondition refusal waits on a file
# another fix creates first, so it does not occur inside the loop. Ambiguity is
# the design's intended refusal. Missing input means the project lacks what the
# fix is built from. Disagreement means the checker flags a violation that the
# extractor cannot locate, which is a gap between the two, not a property of
# the project.
KINDS = (
    ("precondition", ("Dockerfile declares no",)),
    ("ambiguity", ("N different roots", "N different hosts", "N lockfiles", "N database drivers",
                   "N candidate entry points", "bound to N different names")),
    ("disagreement", ("no inline connection string", "no credential literal",
                      "pools by default", "no request targets a literal host")),
    ("missing input", ("not bound to a named identifier", "no entry point is declared",
                       "declares no build script", "no supported database driver")),
)


def kind(why: str) -> str:
    for name, marks in KINDS:
        if any(m in why for m in marks):
            return name
    return "other"


def reason(message: str) -> str:
    """A refusal message with its candidates and counts taken out, so equal
    reasons group together."""
    text = message.split("Candidates:")[0].strip().rstrip(".")
    text = re.sub(r"^[A-Z]{2,4}-\d+:\s*", "", text)
    return re.sub(r"\d+", "N", text)


def examine(path: Path) -> dict[str, object]:
    """Audit one repository and try extraction on its parametric failures."""
    record: dict[str, object] = {"name": path.name}
    try:
        report = audit.run(path)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return record
    record["stack"] = getattr(report.stack, "value", str(report.stack))
    record["score"] = report.score
    # Every scored rule's priority and verdict, so a score can be recomputed
    # under other weights without auditing again.
    record["scored"] = [[r.rule_id, get_rule(r.rule_id).priority.value, r.status.value]
                        for r in report.results if r.scored]
    fails = []
    # The project is read once for every parametric rule, exactly as
    # dispatch.instruct reads it for each, so a large repository is not parsed
    # once per rule.
    source = None
    for result in report.results:
        if result.status.value != "fail":
            continue
        rule = get_rule(result.rule_id)
        entry = {"rule": result.rule_id, "type": rule.fix_type.value,
                 "priority": rule.priority.value, "domain": rule.domain.value}
        if rule.fix_type.value == "DYNAMIC-PARAMETRIC":
            try:
                if source is None:
                    source = extraction.load(str(path))
                extraction.render(source, dispatch.failing(report, result.rule_id))
                entry["extracted"] = True
            except ExtractError as exc:
                entry["extracted"] = False
                entry["refusal"] = reason(str(exc))
            except Exception as exc:
                entry["extracted"] = False
                entry["refusal"] = f"other: {type(exc).__name__}"
        fails.append(entry)
    record["fails"] = fails
    return record


def collect(out: Path, cache: Path = CACHE) -> int:
    """Audit every cached repository not already in out. Returns how many ran."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["name"])
    todo = [p for p in sorted(cache.iterdir()) if p.is_dir() and p.name not in done]
    logger.info("%s already done, %s to go", len(done), len(todo))
    with out.open("a", encoding="utf-8") as fh:
        for count, path in enumerate(todo, 1):
            fh.write(json.dumps(examine(path)) + "\n")
            fh.flush()
            if count % 50 == 0:
                logger.info("%s of %s", count, len(todo))
    return len(todo)


def kinds(why: Counter) -> Counter:
    out: Counter = Counter()
    for text, n in why.items():
        out[kind(text)] += n
    return out


def shares(counts: Counter) -> dict[str, dict[str, float]]:
    total = sum(counts.values())
    return {t: {"count": counts[t], "share": round(counts[t] / total, 4) if total else 0.0}
            for t in TYPES}


def summarise(rows: list[dict]) -> dict[str, object]:
    """Every figure the paper and the response letter quote for RQ2."""
    ok = [r for r in rows if "fails" in r]
    weights = {p.value: w for p, w in audit.WEIGHTS.items()}
    plain: Counter = Counter()
    heavy: Counter = Counter()
    by_stack: dict[str, Counter] = defaultdict(Counter)
    by_priority: dict[str, Counter] = defaultdict(Counter)
    per_repo: dict[str, list[int]] = defaultdict(list)
    rules: Counter = Counter()
    tried = refused = 0
    why: Counter = Counter()
    for r in ok:
        per_repo[r["stack"]].append(len(r["fails"]))
        for f in r["fails"]:
            plain[f["type"]] += 1
            heavy[f["type"]] += weights[f["priority"]]
            by_stack[r["stack"]][f["type"]] += 1
            by_priority[f["priority"]][f["type"]] += 1
            rules[f["rule"]] += 1
            if f["type"] == "DYNAMIC-PARAMETRIC":
                tried += 1
                if not f.get("extracted"):
                    refused += 1
                    why[f.get("refusal", "unknown")] += 1

    def det(counts: Counter) -> float:
        total = sum(counts.values())
        return round((counts["STATIC"] + counts["DYNAMIC-PARAMETRIC"]) / total, 4) if total else 0.0

    def spread(values: list[int]) -> dict[str, float]:
        return {"repos": len(values), "mean": round(statistics.mean(values), 2),
                "median": statistics.median(values), "min": min(values), "max": max(values)}

    return {
        "repos_found": len(rows),
        "repos_audited": len(ok),
        "not_audited": [{"name": r["name"], "error": r["error"]} for r in rows if "error" in r],
        "violations": sum(plain.values()),
        "by_type": shares(plain),
        "deterministic_share": det(plain),
        "by_stack": {s: {"violations": sum(by_stack[s].values()), "by_type": shares(by_stack[s]),
                         "deterministic_share": det(by_stack[s])} for s in STACKS if by_stack[s]},
        "priority_weighted": {"by_type": shares(heavy), "deterministic_share": det(heavy)},
        "by_priority": {p: {"violations": sum(c.values()), "deterministic_share": det(c)}
                        for p, c in sorted(by_priority.items())},
        "per_repo": {s: spread(v) for s, v in per_repo.items()},
        "repos_with_delegated": sum(1 for r in ok
                                    if any(f["type"] == "DYNAMIC-DELEGATED" for f in r["fails"])),
        "single_rule_repos": sum(1 for r in ok if len(r["fails"]) == 1),
        "repos_failing_p0": sum(1 for r in ok if any(f["priority"] == "P0" for f in r["fails"])),
        "rules_seen": len(rules),
        "top_rules": [{"rule": k, "repos": v, "type": get_rule(k).fix_type.value,
                       "priority": get_rule(k).priority.value} for k, v in rules.most_common(10)],
        "extraction": {"parametric_instances": tried, "refused": refused,
                       "refusal_rate": round(refused / tried, 4) if tried else 0.0,
                       "by_kind": {k: {"refused": n, "share_of_parametric":
                                       round(n / tried, 4) if tried else 0.0}
                                   for k, n in sorted(kinds(why).items())},
                       "reasons": dict(why.most_common())},
    }


def markdown(s: dict) -> str:
    def pct(x: float) -> str:
        return f"{100 * x:.1f}%"

    lines = [
        "# RQ2: violations in real repositories by fix type",
        "",
        "Produced by `python -m prodpilot.violations`. Population: the repositories",
        f"module 5.1 kept, {s['repos_found']} found in data/cache, {s['repos_audited']} audited.",
        "",
        "| Fix type | Violations | Share | Priority-weighted share |",
        "| --- | --- | --- | --- |",
    ]
    for t in TYPES:
        lines.append(f"| {t} | {s['by_type'][t]['count']} | {pct(s['by_type'][t]['share'])} | "
                     f"{pct(s['priority_weighted']['by_type'][t]['share'])} |")
    lines += [f"| Total | {s['violations']} | | |", "",
              f"Deterministic share: {pct(s['deterministic_share'])} of violations, "
              f"{pct(s['priority_weighted']['deterministic_share'])} weighted by priority.", "",
              "| Stack | Violations | Deterministic share | Failing rules per repository, mean | median | range |",
              "| --- | --- | --- | --- | --- | --- |"]
    for st, v in s["by_stack"].items():
        p = s["per_repo"][st]
        lines.append(f"| {st} | {v['violations']} | {pct(v['deterministic_share'])} | {p['mean']} | "
                     f"{p['median']} | {p['min']} to {p['max']} |")
    lines += ["", "| Priority | Violations | Deterministic share |", "| --- | --- | --- |"]
    lines += [f"| {k} | {v['violations']} | {pct(v['deterministic_share'])} |"
              for k, v in s["by_priority"].items()]
    lines += ["", f"Repositories with at least one delegated violation: {s['repos_with_delegated']} "
              f"of {s['repos_audited']}. Repositories failing a single rule alone: "
              f"{s['single_rule_repos']}. Repositories failing a P0 rule: {s['repos_failing_p0']}. "
              f"Rules that fire at least once: {s['rules_seen']} of 50.", "",
              "| Most frequent failing rules | Repositories | Fix type | Priority |",
              "| --- | --- | --- | --- |"]
    lines += [f"| {r['rule']} | {r['repos']} | {r['type']} | {r['priority']} |" for r in s["top_rules"]]
    e = s["extraction"]
    lines += ["", f"Extraction on real repositories: {e['refused']} of {e['parametric_instances']} "
              f"parametric instances refused ({pct(e['refusal_rate'])}), against 15 of 74 (20.3%) "
              "on the samples built to be ambiguous.", "",
              "| Kind of refusal | Instances | Share of parametric instances |", "| --- | --- | --- |"]
    lines += [f"| {k} | {v['refused']} | {pct(v['share_of_parametric'])} |"
              for k, v in e["by_kind"].items()]
    lines += ["", "| Refusal reason | Kind | Instances |", "| --- | --- | --- |"]
    lines += [f"| {k} | {kind(k)} | {v} |" for k, v in e["reasons"].items()]
    if s["not_audited"]:
        lines += ["", "Not audited: " + "; ".join(f"{n['name']} ({n['error'][:60]})"
                                                 for n in s["not_audited"])]
    return "\n".join(lines) + "\n"


def main(raw: str, out: str) -> dict:
    collect(Path(raw))
    rows = [json.loads(line) for line in Path(raw).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    found = summarise(rows)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "rq2.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    (target / "rq2.md").write_text(markdown(found), encoding="utf-8")
    return found


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main(sys.argv[1] if len(sys.argv) > 1 else "data/revision/violations.jsonl",
         sys.argv[2] if len(sys.argv) > 2 else "docs/revision")
