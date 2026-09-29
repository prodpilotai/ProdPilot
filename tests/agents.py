"""A two-arm comparison of a live IDE agent with and without ProdPilot.

No IDE agent can run inside an automated test, so this harness prepares each
session, prints the exact text to paste into the agent, and scores the result
afterwards from the files on disk. The person running it drives the agent.

Arms
----
A, agent alone: the project and an ISSUES.md that lists the audit findings in
plain language, with no contracts and no ProdPilot server connected.

B, with ProdPilot: the project, ProdPilot connected through `prodpilot connect`,
and the failing rule identifiers. The agent asks the fix tools for each contract,
applies it and reports it, and ProdPilot's verifier answers.

Both arms see the same findings. Arm B cannot be told to "audit and fix until
the gate decides", because the server exposes no audit or gate tool and the
deploy tool, which runs the gate, creates real infrastructure. The scorer
applies the gate's audit conditions afterwards instead.

Samples
-------
Eight of the 23 committed samples: every sample whose audit shows a delegated
rule once it is a Git repository (node_express_insecure, react_vite_app,
react_vite_noroute, react_vite_ready, amb_unnamed_secret, node_express_secrets),
and two Express samples whose failures are static and parametric only, one
starting near zero (node_express_api) and one with an unusual CORS setup
(node_express_wildcard).

Commands, from the repository root:

    python tests/agents.py prep SAMPLE ARM RUN [--root DIR]
    python tests/agents.py mark WORKSPACE start|end
    python tests/agents.py score WORKSPACE
    python tests/agents.py summary [--root DIR] [--out docs/revision]

The protocol a person follows is in docs/revision/agent_protocol.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "samples"
DEFAULT_ROOT = Path("E:/tmp/agents") if sys.platform == "win32" else Path("/tmp/agents")
CHOSEN = ("node_express_insecure", "react_vite_app", "react_vite_noroute", "react_vite_ready",
          "amb_unnamed_secret", "node_express_secrets", "node_express_api",
          "node_express_wildcard")
ARMS = ("A", "B")
TYPES = ("STATIC", "DYNAMIC-PARAMETRIC", "DYNAMIC-DELEGATED")
META = "harness.json"
REPORT = "agent_report.txt"
SCORE = "score.json"
ASK_A = ("Make this project production ready by fixing the issues listed in ISSUES.md. "
         "Do not edit ISSUES.md, harness.json or agent_report.txt. When you finish, end your "
         "reply with one line per issue in the form FIXED: <ID> or NOT FIXED: <ID>.")
ASK_B = ("This project fails these ProdPilot rules: {ids}. Use the prodpilot MCP tools. For each "
         "rule call prodpilot_fix_instruction with project_path {path} and the rule, make the "
         "change it asks for, then call prodpilot_fix_applied with the same path. Retry a rule "
         "at most three times while the outcome is unresolved. Do not call prodpilot_deploy. "
         "Do not edit harness.json or agent_report.txt. When you finish, end your reply with "
         "one line per rule in the form FIXED: <ID> or NOT FIXED: <ID>.")
KEEP = {META, REPORT, SCORE, "ISSUES.md"}


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=harness", "-c", "user.email=harness@local",
                           *args], cwd=path, capture_output=True, text=True).stdout


def statuses(path: Path) -> dict[str, dict]:
    """Every scored rule's verdict and fix type, from the frozen audit."""
    from prodpilot import audit

    found = audit.run(path).to_dict()
    out = {}
    for issue in found["issues"]:
        out[issue["rule_id"]] = {"status": "fail", "type": issue["fix_type"],
                                 "priority": issue["priority"],
                                 "requirement": issue["requirement"], "detail": issue["detail"],
                                 "files": sorted({loc.get("file", "") for loc in issue["locations"]
                                                  if loc.get("file")})}
    for rid in found["passed_rules"]:
        rid = rid if isinstance(rid, str) else rid["rule_id"]
        out[rid] = {"status": "pass"}
    out["_score"] = {"score": found["score"]}
    return out


def issues_text(before: dict[str, dict]) -> str:
    lines = ["# Issues to fix", ""]
    for rid, v in sorted(before.items()):
        if rid.startswith("_") or v["status"] != "fail":
            continue
        where = ", ".join(v["files"]) or "the project"
        lines.append(f"- [{rid}] {v['requirement'][:1].upper()}{v['requirement'][1:]}. "
                     f"Found: {v['detail']}. Where: {where}.")
    return "\n".join(lines) + "\n"


def prep(sample: str, arm: str, run: int, root: Path = DEFAULT_ROOT) -> Path:
    if sample not in CHOSEN or arm not in ARMS:
        raise SystemExit(f"sample must be one of {CHOSEN} and arm one of {ARMS}")
    work = root / sample / arm / f"run{run}"
    if work.exists():
        raise SystemExit(f"{work} exists; delete it to prepare the session again")
    shutil.copytree(SAMPLES / sample, work)
    git(work, "init", "-q", ".")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "baseline")
    before = statuses(work)
    failing = sorted(r for r, v in before.items() if not r.startswith("_") and v["status"] == "fail")
    if arm == "A":
        (work / "ISSUES.md").write_text(issues_text(before), encoding="utf-8")
        ask = ASK_A
    else:
        ask = ASK_B.format(ids=", ".join(failing), path=str(work))
    meta = {"sample": sample, "arm": arm, "run": run, "baseline": git(work, "rev-parse", "HEAD").strip(),
            "before": before, "failing": failing, "prompt": ask, "prepared": now()}
    (work / META).write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"Workspace: {work}\n\nPaste this into the agent:\n\n{ask}\n")
    return work


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mark(work: Path, which: str) -> None:
    meta = json.loads((work / META).read_text(encoding="utf-8"))
    meta[which] = now()
    (work / META).write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"{which} recorded for {work}")


def claims(text: str) -> dict[str, bool]:
    """FIXED and NOT FIXED lines from the agent's final message."""
    out = {}
    for hit in re.finditer(r"^\s*[-*]?\s*(NOT FIXED|FIXED)\s*:\s*([A-Z]{2,4}-\d{3})", text, re.M | re.I):
        out[hit.group(2).upper()] = hit.group(1).upper() == "FIXED"
    return out


def outcomes(before: dict, after: dict) -> dict[str, list[str]]:
    """Each rule's outcome from the audits before and after the session."""
    out = defaultdict(list)
    for rid, v in before.items():
        if rid.startswith("_"):
            continue
        now_status = after.get(rid, {}).get("status", "absent")
        if v["status"] == "fail":
            out["fixed" if now_status == "pass" else "still_failing"].append(rid)
        elif v["status"] == "pass" and now_status == "fail":
            out["newly_broken"].append(rid)
    return {k: sorted(v) for k, v in out.items()}


def normal_hash(data: bytes) -> str:
    """A hash that ignores whitespace, so formatting alone is not a difference."""
    return hashlib.sha256(re.sub(rb"\s+", b" ", data).strip()).hexdigest()[:16]


def changed(work: Path, base: str) -> dict[str, object]:
    stat = git(work, "diff", "--numstat", base)
    added = removed = 0
    files = set()
    for line in stat.splitlines():
        a, r, name = (line.split("\t") + ["", "", ""])[:3]
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
        files.add(name)
    for line in git(work, "ls-files", "--others", "--exclude-standard").splitlines():
        files.add(line)
        try:
            added += len((work / line).read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            pass
    files = {f for f in files if f and f not in KEEP and not f.startswith(".vscode/")
             and not f.startswith(".cursor/") and not f.startswith(".devin/")}
    hashes = {f: normal_hash((work / f).read_bytes()) for f in sorted(files) if (work / f).is_file()}
    kept = subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=work,
                          capture_output=True).returncode == 0
    commits = git(work, "rev-list", "--count", f"{base}..HEAD").strip() if kept else ""
    return {"files": sorted(files), "lines_added": added, "lines_removed": removed,
            "hashes": hashes, "new_commits": int(commits) if commits.isdigit() else None,
            "history_rewritten": not kept}


def score(work: Path) -> dict:
    meta = json.loads((work / META).read_text(encoding="utf-8"))
    text = (work / REPORT).read_text(encoding="utf-8") if (work / REPORT).exists() else ""
    after = statuses(work)
    result = outcomes(meta["before"], after)
    said = claims(text)
    claimed = sorted(r for r, ok in said.items() if ok)
    over = sorted(r for r in claimed if r in result.get("still_failing", []))
    p0 = sum(1 for r, v in after.items() if not r.startswith("_") and v["status"] == "fail"
             and v.get("priority") == "P0")
    seconds = None
    if meta.get("start") and meta.get("end"):
        seconds = (datetime.fromisoformat(meta["end"]) - datetime.fromisoformat(meta["start"])).total_seconds()
    by_type = {}
    for t in TYPES:
        target = [r for r in meta["failing"] if meta["before"][r]["type"] == t]
        by_type[t] = {"failing": len(target), "fixed": sum(1 for r in target if r in result.get("fixed", []))}
    found = {"sample": meta["sample"], "arm": meta["arm"], "run": meta["run"],
             "report_found": bool(text), "outcomes": result, "by_type": by_type,
             "claimed_fixed": claimed, "overclaims": over,
             "score_before": meta["before"]["_score"]["score"], "score_after": after["_score"]["score"],
             "p0_after": p0, "gate_audit_conditions": p0 == 0 and after["_score"]["score"] >= 90,
             "diff": changed(work, meta["baseline"]), "seconds": seconds,
             "timeout": bool(seconds and seconds > 20 * 60)}
    (work / SCORE).write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in found.items() if k != "diff"}, indent=1))
    return found


def summary(root: Path = DEFAULT_ROOT, out: Path = ROOT / "docs" / "revision") -> dict:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob(f"*/*/run*/{SCORE}"))]
    arms = {}
    for arm in ARMS:
        mine = [r for r in runs if r["arm"] == arm]
        if not mine:
            continue
        per_type = {t: {"failing": sum(r["by_type"][t]["failing"] for r in mine),
                        "fixed": sum(r["by_type"][t]["fixed"] for r in mine)} for t in TYPES}
        for v in per_type.values():
            v["rate"] = round(v["fixed"] / v["failing"], 4) if v["failing"] else None
        claimed = sum(len(r["claimed_fixed"]) for r in mine)
        over = sum(len(r["overclaims"]) for r in mine)
        arms[arm] = {"runs": len(mine), "by_type": per_type,
                     "regressions": sum(len(r["outcomes"].get("newly_broken", [])) for r in mine),
                     "runs_with_regression": sum(1 for r in mine if r["outcomes"].get("newly_broken")),
                     "claimed_fixed": claimed, "overclaims": over,
                     "overclaim_rate": round(over / claimed, 4) if claimed else None,
                     "gate_audit_conditions_met": sum(1 for r in mine if r["gate_audit_conditions"]),
                     "timeouts": sum(1 for r in mine if r["timeout"]),
                     "median_seconds": median([r["seconds"] for r in mine if r["seconds"]])}
    variance = {}
    for (sample, arm), group in grouped(runs).items():
        if len(group) < 2:
            continue
        files = set().union(*(r["diff"]["hashes"] for r in group))
        same = {f: len({r["diff"]["hashes"].get(f) for r in group}) == 1 for f in files}
        variance[f"{sample}/{arm}"] = {"runs": len(group), "files": len(files),
                                       "identical_files": sum(same.values())}
    found = {"runs": len(runs), "arms": arms, "variance": variance}
    out.mkdir(parents=True, exist_ok=True)
    (out / "agent.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    (out / "agent.md").write_text(markdown(found), encoding="utf-8")
    return found


def grouped(runs: list[dict]) -> dict[tuple, list[dict]]:
    out = defaultdict(list)
    for r in runs:
        out[(r["sample"], r["arm"])].append(r)
    return out


def median(values: list[float]):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def markdown(s: dict) -> str:
    lines = ["# Live-agent comparison", "", f"Scored runs: {s['runs']}.", "",
             "| Measure | Arm A, agent alone | Arm B, with ProdPilot |", "| --- | --- | --- |"]
    a, b = s["arms"].get("A", {}), s["arms"].get("B", {})

    def cell(arm: dict, t: str) -> str:
        v = arm.get("by_type", {}).get(t)
        return f"{v['fixed']} of {v['failing']}" if v else "n/a"

    for t in TYPES:
        lines.append(f"| {t} fixed | {cell(a, t)} | {cell(b, t)} |")
    for k, label in (("regressions", "Rules newly broken"), ("overclaims", "Claimed fixed but still failing"),
                     ("overclaim_rate", "Overclaim rate"), ("gate_audit_conditions_met", "Runs meeting the gate's audit conditions"),
                     ("timeouts", "Timeouts"), ("median_seconds", "Median seconds")):
        lines.append(f"| {label} | {a.get(k, 'n/a')} | {b.get(k, 'n/a')} |")
    lines += ["", "Run-to-run variance: files whose normalised content is identical across runs.", "",
              "| Sample and arm | Runs | Files changed | Identical across runs |", "| --- | --- | --- | --- |"]
    lines += [f"| {k} | {v['runs']} | {v['files']} | {v['identical_files']} |" for k, v in s["variance"].items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    root = DEFAULT_ROOT
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1])
    if not argv:
        raise SystemExit(__doc__)
    if argv[0] == "prep":
        prep(argv[1], argv[2].upper(), int(argv[3]), root)
    elif argv[0] == "mark":
        mark(Path(argv[1]), argv[2])
    elif argv[0] == "score":
        score(Path(argv[1]))
    elif argv[0] == "summary":
        out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / "docs" / "revision"
        print(json.dumps(summary(root, out), indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
