"""What the deployment label measures, from the labelled data alone.

The label is whether a Render free plan service became live and answered its
health path. This module asks three questions of it, all correlational:

- for each rule and each domain, do real repositories that fail it deploy less
  often than those that pass it;
- for each synthetic negative, did breaking one rule change the outcome of the
  repository it was made from;
- for projects that built locally but did not deploy, where did the deploy stop,
  as far as the stored stage and detail can tell.

Render's own build and runtime logs were not stored at labelling time and the
services were deleted afterwards, so the third question is answered by stage,
not by cause. Rule outcomes come from the audit records written by
prodpilot.violations.

    python -m prodpilot.labelstats data/revision docs/revision
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from prodpilot.rules import get_rule

DATA = Path("data")
STACKS = ("node_express", "react_vite")
Z = 1.959964


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float, float]:
    """A rate and its Wilson interval, 95% by default."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), min(1.0, centre + half)


def gap(k1: int, n1: int, k2: int, n2: int, z: float = Z) -> tuple[float, float, float]:
    """Difference of two rates with Newcombe's interval built from Wilson's."""
    p1, l1, u1 = wilson(k1, n1, z)
    p2, l2, u2 = wilson(k2, n2, z)
    d = p1 - p2
    return (d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def normal_quantile(p: float) -> float:
    """The standard normal quantile, by bisection on the error function."""
    low, high = 0.0, 10.0
    for _ in range(100):
        mid = (low + high) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def stage_of(stage: str, detail: str) -> str:
    """Where a failed deploy stopped, from the stored stage and detail."""
    text = f"{stage} {detail}".lower()
    if "still building" in text:
        return "timed out while Render was building"
    if "health probe" in text or "smoke" in stage.lower():
        return "went live, health path never answered"
    if "deploy monitoring" in text and "failed" in text:
        return "Render reported the deploy failed"
    if "never attempted" in text or "servi" in text and "error" in text:
        return "service could not be created"
    return "other"


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def run(raw: Path, data: Path = DATA) -> dict:
    labels = {(r["name"], r["kind"], r.get("rule_id", "")): r for r in read(data / "labels.jsonl")}
    builds = {(r["name"], r["kind"], r.get("rule_id", "")): r for r in read(data / "builds.jsonl")}
    audits = {r["name"]: r for r in read(raw / "violations.jsonl") if "scored" in r}

    # Real repositories with both an audit record and a label.
    real = []
    for (name, kind, _), lab in labels.items():
        if kind != "repo":
            continue
        a = audits.get(name.replace("/", "__"))
        if a:
            real.append({"name": name, "stack": a["stack"], "label": int(lab["label"]),
                         "status": {rid: st for rid, _, st in a["scored"]}})

    per_rule, per_domain = [], []
    # Fifty rule tests at 95% would flag about two by chance, so each flag is
    # also checked at the Bonferroni level for the number of tests run.
    tests = sum(len({rid for r in real if r["stack"] == s for rid in r["status"]}) for s in STACKS)
    strict = normal_quantile(1 - 0.025 / max(tests, 1))
    for stack in STACKS:
        group = [r for r in real if r["stack"] == stack]
        rules = sorted({rid for r in group for rid in r["status"]})
        for rid in rules:
            failing = [r for r in group if r["status"].get(rid) == "fail"]
            passing = [r for r in group if r["status"].get(rid) == "pass"]
            per_rule.append(row(stack, rid, failing, passing, strict))
        domains = sorted({get_rule(rid).domain.value for rid in rules})
        for dom in domains:
            ids = [rid for rid in rules if get_rule(rid).domain.value == dom]
            failing = [r for r in group if any(r["status"].get(i) == "fail" for i in ids)]
            passing = [r for r in group if all(r["status"].get(i) == "pass" for i in ids)
                       and any(i in r["status"] for i in ids)]
            per_domain.append(row(stack, dom, failing, passing, strict))

    # Synthetic negatives against the repository each was made from. A negative
    # is named owner__repo_rule; GitHub user names hold no underscore, so the
    # first double underscore always separates owner from repository.
    pairs = []
    for (name, kind, rid), lab in labels.items():
        if kind != "negative":
            continue
        source = labels.get((origin(name, rid), "repo", ""))
        if source is not None:
            pairs.append({"rule": rid, "source": int(source["label"]), "negative": int(lab["label"])})
    moves = Counter((p["source"], p["negative"]) for p in pairs)
    by_rule: dict[str, Counter] = defaultdict(Counter)
    for p in pairs:
        by_rule[p["rule"]][(p["source"], p["negative"])] += 1

    # Built locally, did not deploy: where the deploy stopped.
    stops: dict[str, Counter] = defaultdict(Counter)
    for key, lab in labels.items():
        b = builds.get(key)
        if lab["label"] == 0 and b and b.get("built") == 1:
            where = f"{b.get('stack', '')}_{'real' if key[1] == 'repo' else 'negative'}"
            stops[where][stage_of(str(lab.get("stage", "")), str(lab.get("detail", "")))] += 1

    real_rate = [r["label"] for r in real]
    neg = [int(l["label"]) for k, l in labels.items() if k[1] == "negative"]
    return {
        "real_repos": len(real),
        "real_deploy_rate": round(sum(real_rate) / len(real_rate), 4) if real_rate else None,
        "negative_rows": len(neg),
        "negative_deploy_rate": round(sum(neg) / len(neg), 4) if neg else None,
        "per_rule": per_rule,
        "per_domain": per_domain,
        "rule_tests": len(per_rule),
        "expected_chance_flags": round(0.05 * len(per_rule), 1),
        "flagged_rules": [r for r in per_rule if r["excludes_zero"]],
        "flagged_domains": [r for r in per_domain if r["excludes_zero"]],
        "negatives": {
            "paired": len(pairs),
            "unchanged": moves[(0, 0)] + moves[(1, 1)],
            "deployed_to_failed": moves[(1, 0)],
            "failed_to_deployed": moves[(0, 1)],
            "both_failed": moves[(0, 0)],
            "both_deployed": moves[(1, 1)],
            "by_rule": {r: {"paired": sum(c.values()), "deployed_to_failed": c[(1, 0)],
                            "failed_to_deployed": c[(0, 1)], "source_deployed": c[(1, 0)] + c[(1, 1)]}
                        for r, c in sorted(by_rule.items())},
        },
        "built_not_deployed": {k: dict(v.most_common()) for k, v in sorted(stops.items())},
        "logs": "Render build and runtime logs were not stored at labelling time, and every "
                "service was deleted after labelling, so causes are reported by stage.",
    }


def origin(name: str, rule: str) -> str:
    """The source repository of a synthetic negative, as the labels name it."""
    base = name[: -(len(rule) + 1)] if name.lower().endswith("_" + rule.lower()) else name
    owner, _, repo = base.partition("__")
    return f"{owner}/{repo}"


def row(stack: str, what: str, failing: list, passing: list, z: float = Z) -> dict:
    kf, nf = sum(r["label"] for r in failing), len(failing)
    kp, np_ = sum(r["label"] for r in passing), len(passing)
    rf, lf, uf = wilson(kf, nf)
    rp, lp, up = wilson(kp, np_)
    both = bool(nf and np_)
    d, dl, du = gap(kf, nf, kp, np_) if both else (0.0, 0.0, 0.0)
    _, sl, su = gap(kf, nf, kp, np_, z) if both else (0.0, 0.0, 0.0)
    return {"stack": stack, "what": what,
            "fail": {"deployed": kf, "of": nf, "rate": round(rf, 4), "low": round(lf, 4), "high": round(uf, 4)},
            "pass": {"deployed": kp, "of": np_, "rate": round(rp, 4), "low": round(lp, 4), "high": round(up, 4)},
            "difference": round(d, 4), "low": round(dl, 4), "high": round(du, 4),
            "excludes_zero": both and (dl > 0 or du < 0),
            "excludes_zero_corrected": both and (sl > 0 or su < 0)}


def markdown(s: dict) -> str:
    n = s["negatives"]
    lines = ["# What the deployment label measures", "",
             "Produced by `python -m prodpilot.labelstats`. All results are correlational.", "",
             f"Real repositories with an audit and a label: {s['real_repos']}, deploy rate "
             f"{100 * s['real_deploy_rate']:.1f}%. Synthetic negatives: {s['negative_rows']}, "
             f"deploy rate {100 * s['negative_deploy_rate']:.1f}%.", "",
             "## Rules and domains whose failure changes the deploy rate", "",
             "Rate among repositories failing it against those passing it, with a Newcombe 95% "
             "interval on the difference. Listed where the interval excludes zero.", "",
             "| Stack | Rule or domain | Deployed when failing | Deployed when passing | Difference | 95% CI | Holds after Bonferroni |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in s["flagged_domains"] + s["flagged_rules"]:
        lines.append(f"| {r['stack']} | {r['what']} | {r['fail']['deployed']} of {r['fail']['of']} | "
                     f"{r['pass']['deployed']} of {r['pass']['of']} | {r['difference']:+.3f} | "
                     f"{r['low']:+.3f} to {r['high']:+.3f} | "
                     f"{'yes' if r['excludes_zero_corrected'] else 'no'} |")
    lines += ["", f"Rules tested: {len(s['per_rule'])} rule and stack pairs; "
              f"{len(s['flagged_rules'])} show a difference whose interval excludes zero, "
              f"against about {s['expected_chance_flags']} expected by chance at 95%. "
              f"Domains tested: {len(s['per_domain'])}; {len(s['flagged_domains'])} excluding zero.",
              "", "## Synthetic negatives against their source repository", "",
              f"Paired: {n['paired']}. Outcome unchanged: {n['unchanged']} "
              f"(both failed {n['both_failed']}, both deployed {n['both_deployed']}). "
              f"Source deployed but negative failed: {n['deployed_to_failed']}. "
              f"Source failed but negative deployed: {n['failed_to_deployed']}.", "",
              "| Broken rule | Pairs | Source deployed | Deployed to failed | Failed to deployed |",
              "| --- | --- | --- | --- | --- |"]
    lines += [f"| {r} | {v['paired']} | {v['source_deployed']} | {v['deployed_to_failed']} | "
              f"{v['failed_to_deployed']} |" for r, v in n["by_rule"].items()]
    lines += ["", "## Built locally but did not deploy: where the deploy stopped", "", s["logs"], "",
              "| Group | Where it stopped | Projects |", "| --- | --- | --- |"]
    for g, by in s["built_not_deployed"].items():
        lines += [f"| {g} | {k} | {v} |" for k, v in by.items()]
    return "\n".join(lines) + "\n"


def main(raw: str, out: str) -> dict:
    found = run(Path(raw))
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "label.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    (target / "label.md").write_text(markdown(found), encoding="utf-8")
    return found


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/revision",
         sys.argv[2] if len(sys.argv) > 2 else "docs/revision")
