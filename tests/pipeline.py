"""The whole chain, run on one sample at a time, with every stage recorded.

Module 7.3 asks whether audit, fix loop, scoring gate and the nine ProdPush
stages work as one chain across many projects, broken and clean, rather than on
one demo. This runs that chain for a sample and returns a trace of what each
part did, and it is how the run recorded in docs/pipeline.md was produced.

What is real and what is not
----------------------------
Real: the audit, every fix contract, the edits they cause, the verifier that
decides each fix, the regression guard, the loop and its budget, the gate with
the promoted model and a real build of the project in Docker, and all nine
ProdPush stages with their own code. The project is a real Git repository, as
a developer's project is, and stage 4 pushes to a real bare repository on disk.
Stage 3 builds the real image and runs the real container.

Substituted, exactly as tests/test_prodpush.py substitutes them, because no run
here may touch a live service: the Render API, the GitHub API, and the deployed
application stage 7 probes. The origin remote names GitHub, and pushes to it are
rewritten to the local mirror.

The agent. In production the IDE agent applies each contract. Here tests/apply.py
applies a content contract literally, which is all an agent can correctly do with
one. It cannot author a constraint contract, so a DYNAMIC-DELEGATED rule comes
back unapplied and the loop records it for manual review. That is a limit of
this run, not a result about delegated fixes.

Nothing is caught and hidden. An exception anywhere in the chain is recorded with
its traceback as an unhandled failure, which is what Section 8 asks to find.

Usage, from the repository root, which holds the model the gate loads:

    python tests/pipeline.py OUT.json [SAMPLE ...]
    python tests/pipeline.py --markdown OUT.json
    python tests/pipeline.py --metrics OUT.json

--markdown gives the tables in docs/pipeline.md, and --metrics the fix
reliability tables in docs/metrics.md, both from the recorded traces.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from apply import applier  # noqa: E402
from test_prodpush import KEY, SHIPPED, TOKEN, github_api, render_api, served  # noqa: E402

from prodpilot import audit, buildtest, config, gate, prodpush, push, rules  # noqa: E402
from nacl import encoding, public  # noqa: E402
from prodpilot.dispatch import Fixer  # noqa: E402
from prodpilot.render import Render  # noqa: E402
from prodpilot.verify import verifier  # noqa: E402

REPO = HERE.parent
SAMPLES = HERE / "samples"
LIVE = "https://demo-api.onrender.com"

# A real repository key, as GitHub returns one, so stage 8 seals against a key
# that can be used. tests/test_prodpush.py makes its own the same way.
PUBLIC = public.PrivateKey.generate().public_key.encode(encoding.Base64Encoder()).decode()

# The entry page `npm create vite` writes. react_vite_ready ships without one,
# so Vite cannot build it and the gate rightly refuses it, and its committed
# Dockerfile runs npm ci with no lockfile to read. No committed sample is a clean
# React project that can build, so the variant adds the two things any real one
# has: this entry page, mounting at the #root its src/main.jsx renders into, and
# a package-lock.json npm writes itself. The committed fixture is not changed.
ENTRY = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Reports</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

# A run name that is not a sample directory names a sample and the files added.
VARIANTS = {"react_vite_ready+entry": ("react_vite_ready", {"index.html": ENTRY}, True)}


def lock(root: Path) -> None:
    """Have npm write the project's package-lock.json, as npm install would."""
    subprocess.run(("docker", "run", "--rm", "-v", f"{root}:/app", "-w", "/app", "node:20",
                    "npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit",
                    "--no-fund"), check=True, capture_output=True, timeout=600)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(("git",) + args, cwd=str(root), check=True,
                          capture_output=True, text=True)
    return done.stdout.strip()


def prepare(name: str, work: Path) -> tuple[Path, Path]:
    """A copy of the sample in a real repository, with a local mirror for its pushes."""
    sample, extra, locked = VARIANTS.get(name, (name, {}, False))
    root = work / name
    shutil.copytree(SAMPLES / sample, root)
    for path, text in extra.items():
        (root / path).write_text(text, encoding="utf-8")
    if locked:
        lock(root)

    mirror = work / f"{name}.git"
    subprocess.run(("git", "init", "--bare", "-q", str(mirror)), check=True, capture_output=True)
    subprocess.run(("git", "-C", str(mirror), "symbolic-ref", "HEAD", "refs/heads/main"),
                   check=True, capture_output=True)

    origin = f"https://github.com/octo/{name.replace('+', '-')}.git"
    git(root, "init", "-q")
    git(root, "config", "user.email", "developer@example.com")
    git(root, "config", "user.name", "Developer")
    git(root, "config", "core.autocrlf", "false")
    git(root, "remote", "add", "origin", origin)
    git(root, "config", f"url.{mirror.as_posix()}.pushInsteadOf",
        push.authed(push.https_url(origin), TOKEN))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the application")
    git(root, "branch", "-M", "main")
    return root, mirror


def fix_type(rule_id: str) -> str:
    rule = rules.get_rule(rule_id)
    return rule.fix_type.value if rule else "unknown"


def by_type(rule_ids) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for rule_id in rule_ids:
        grouped.setdefault(fix_type(rule_id), []).append(rule_id)
    return grouped


def audited(report) -> dict:
    return {
        "stack": report.stack.value if report.stack else None,
        "score": report.score,
        "failing": by_type(r.rule_id for r in report.issues),
        "blockers": [r.rule_id for r in report.blockers],
    }


def trace(name: str, work: Path) -> dict:
    """Run the whole chain on one sample and record what every part did."""
    record: dict = {"sample": name, "unhandled": None}
    started = time.monotonic()
    step = "prepare"
    try:
        root, mirror = prepare(name, work)
        step = "audit"
        try:
            record["start"] = audited(audit.run(root))
        except Exception as exc:  # an unauditable project is an outcome, recorded as one
            record["start"] = {"stack": None, "score": None, "failing": {}, "blockers": [],
                               "refused": f"{type(exc).__name__}: {exc}"}

        step = "loop and gate"
        fixer = Fixer(root, applier(root), verifier(root))
        decision = gate.run(root, fixer.resolve)
        record["gate"] = {
            "ready": decision.ready, "reason": decision.reason, "score": decision.score,
            "band": decision.band, "chance": decision.chance, "operating": decision.operating,
            "cycles": decision.cycles, "stopped": decision.stopped,
            "resolved": by_type(decision.resolved),
            "review": [{"rule_id": r.rule_id, "fix_type": r.fix_type, "attempts": r.attempts,
                        "reason": r.reason} for r in decision.review],
            "blockers": list(decision.blockers),
        }
        # Every contract sent, in order: what the agent claimed and what the verifier found.
        record["applied"] = [{"rule_id": c.rule_id, "fix_type": fix_type(c.rule_id),
                              "claimed": c.applied, "verified": v, "said": c.summary}
                             for c, v in zip(fixer.claims, fixer.verdicts)]
        record["regressions"] = [r.detail for r in fixer.regressions]
        # Every rule as the loop left the project, before anything is committed or
        # pushed: a rule can pass here without being attempted again, when a later
        # fix created the file its own contract needed.
        try:
            left = audit.run(root)
            record["end"] = {**audited(left),
                             "status": {r.rule_id: r.status.value for r in left.results}}
        except Exception as exc:
            record["end"] = {"refused": f"{type(exc).__name__}: {exc}"}

        step = "commit"
        if git(root, "status", "--porcelain"):
            git(root, "add", "-A")
            git(root, "commit", "-qm", "apply the fixes ProdPilot verified")
            record["committed"] = True
        else:
            record["committed"] = False

        step = "prodpush"
        fetch, calls = render_api(SHIPPED)
        probe, asked = served(LIVE)
        hub, secrets = github_api(PUBLIC)
        shipped = prodpush.run(root, provider=Render(api_key=KEY, fetch=fetch), fetch=probe,
                               github=hub, token=TOKEN, build=buildtest.run,
                               sleep=lambda s: None, clock=lambda: 0.0)
        record["prodpush"] = {
            "ok": shipped.ok, "failed_stage": shipped.to_dict().get("failed_stage"),
            "steps": [{"stage": s["stage"], "ok": s["ok"], "ran": s["ran"], "detail": s["detail"]}
                      for s in shipped.to_dict()["steps"]],
            "render_calls": len(calls), "smoke_requests": len(asked),
            "secrets_sealed": len(secrets),
            "mirror_commits": int(git(mirror, "rev-list", "--count", "main")) if pushed(mirror) else 0,
        }
    except Exception as exc:
        record["unhandled"] = {"step": step, "error": f"{type(exc).__name__}: {exc}",
                               "traceback": traceback.format_exc()}
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def pushed(mirror: Path) -> bool:
    return subprocess.run(("git", "-C", str(mirror), "rev-parse", "--verify", "-q", "main"),
                          capture_output=True).returncode == 0


def writable(remove, path, exc) -> None:
    """Git marks its object files read only, which Windows will not delete."""
    os.chmod(path, stat.S_IWRITE)
    remove(path)


def main(out: Path, names: list[str]) -> None:
    os.chdir(REPO)  # the gate loads data/model.joblib relative to here
    work = out.parent / (out.stem + "-projects")
    if work.exists():
        shutil.rmtree(work, onerror=writable)
    work.mkdir(parents=True)
    os.environ[config.CONFIG_HOME_ENV_VAR] = str(work / "home")
    config.save_credentials(config.Credentials(github_token=TOKEN, render_api_key=KEY))

    names = names or sorted([p.name for p in SAMPLES.iterdir() if p.is_dir()] + list(VARIANTS))
    traces = []
    for name in names:
        record = trace(name, work)
        traces.append(record)
        out.write_text(json.dumps(traces, indent=1), encoding="utf-8")
        push_ = record.get("prodpush") or {}
        print(f"{name}: start {record.get('start', {}).get('score')}, "
              f"gate {'ready' if record.get('gate', {}).get('ready') else 'refused'} "
              f"at {record.get('gate', {}).get('score')}, prodpush "
              f"{'wired' if push_.get('ok') else 'stopped at ' + str(push_.get('failed_stage'))}"
              f"{', UNHANDLED ' + record['unhandled']['error'] if record['unhandled'] else ''}"
              f" ({record['seconds']}s)", flush=True)


STAGES = ("scoring gate", "pre-flight", "environment sealing", "docker build test",
          "git push", "render deployment", "deploy monitoring", "post-deploy smoke test",
          "ci/cd wiring")
HEADS = ("Gate", "Pre-flight", "Sealing", "Build", "Push", "Deploy", "Monitor", "Smoke", "CI/CD")
SHORT = {"STATIC": "S", "DYNAMIC-PARAMETRIC": "P", "DYNAMIC-DELEGATED": "D"}


def mark(step: dict | None) -> str:
    if step is None:
        return "-"
    if step["ran"]:
        return "pass" if step["ok"] else "FAIL"
    return "n/a" if step["ok"] else "not reached"


def markdown(traces: list[dict]) -> str:
    """The run as tables: one row per sample, then every stage, then the reasons."""
    out = ["| Sample | Stack | Start | Cycles | Fixed S/P/D | Review | Gate score | Gate | ProdPush |",
           "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for t in traces:
        start, g, p = t.get("start") or {}, t.get("gate") or {}, t.get("prodpush") or {}
        fixed = "/".join(str(len(g.get("resolved", {}).get(k, []))) for k in SHORT)
        shipped = ("wired" if p.get("ok") else f"stopped at {p.get('failed_stage')}") if p else "-"
        if t.get("unhandled"):
            shipped = f"UNHANDLED in {t['unhandled']['step']}"
        out.append(f"| `{t['sample']}` | {start.get('stack') or 'none'} | {start.get('score')} | "
                   f"{g.get('cycles', '-')} | {fixed} | {len(g.get('review', []))} | "
                   f"{g.get('score')} | {'ready' if g.get('ready') else 'refused'} | {shipped} |")

    out += ["", "| Sample | " + " | ".join(HEADS) + " |",
            "| --- |" + " --- |" * len(STAGES)]
    for t in traces:
        steps = {s["stage"]: s for s in (t.get("prodpush") or {}).get("steps", [])}
        out.append(f"| `{t['sample']}` | " + " | ".join(mark(steps.get(s)) for s in STAGES) + " |")

    for t in traces:
        g, p = t.get("gate") or {}, t.get("prodpush") or {}
        out += ["", f"### `{t['sample']}`", "", f"Gate: {g.get('reason')}"]
        stopped = next((s for s in p.get("steps", []) if s["ran"] and not s["ok"]), None)
        if stopped and stopped["stage"] != "scoring gate":
            out += ["", f"Stopped at {stopped['stage']}: {stopped['detail']}"]
        elif p.get("ok"):
            out += ["", f"Wired: {p.get('mirror_commits')} commit(s) pushed to the mirror, "
                        f"{p.get('render_calls')} Render call(s), {p.get('secrets_sealed')} "
                        f"secret(s) sealed for GitHub."]
        if g.get("review"):
            # A rule retired in one cycle can pass once a later fix creates what it
            # needed, so the record says which ones did.
            status = (t.get("end") or {}).get("status", {})
            out += ["", "Manual review:", ""]
            out += [f"- {r['rule_id']} ({r['fix_type']}): {r['reason']}"
                    + (". It passes by the end of the run." if status.get(r["rule_id"]) == "pass" else "")
                    for r in g["review"]]
        if t.get("unhandled"):
            out += ["", f"Unhandled in {t['unhandled']['step']}: {t['unhandled']['error']}"]
    return "\n".join(out) + "\n"


BUDGET = 3  # the loop's attempts per issue
TYPES = ("STATIC", "DYNAMIC-PARAMETRIC", "DYNAMIC-DELEGATED")


def cause(said: str) -> str:
    """Why a rule was not verified within budget, from what the executor said."""
    if said.startswith("no file at"):
        return "the file it edits was not created yet"
    if said.startswith("could not locate the anchor"):
        return "the executor could not place the anchor"
    if "constraint contract needs an author" in said:
        return "no author for a constraint contract"
    if "reverted by the regression guard" in said:
        return "verified, then reverted by the regression guard"
    return "applied, and the rule still failed"


def measured(traces: list[dict]) -> dict:
    """Module 7.4's fix reliability, per fix type, from a run's recorded contracts.

    An instance is one rule on one sample the loop sent a contract for. A
    verdict is recorded before the regression guard runs, so a fix the verifier
    passed and the guard then reverted still reads verified; each recorded
    regression names the rule whose fix was reverted, and that many of its
    verified attempts are counted as reverted, never as successes.
    """
    rows = {kind: {"instances": 0, "sent": 0, "first": 0, "budget": 0, "end": 0,
                   "applied": 0, "applied_verified": 0, "refused": 0,
                   "causes": {}, "refusals": {}} for kind in TYPES}
    for t in traces:
        status = (t.get("end") or {}).get("status", {})
        reverted: dict[str, int] = {}
        for detail in t.get("regressions", []):
            rule_id = detail.split(" broke ")[0].replace("fixing ", "").strip()
            reverted[rule_id] = reverted.get(rule_id, 0) + 1
        attempts: dict[str, list[dict]] = {}
        for a in t.get("applied", []):
            a = dict(a)
            if a["verified"] and reverted.get(a["rule_id"], 0) > 0:
                reverted[a["rule_id"]] -= 1
                a["verified"] = False
                a["said"] = "verified, then reverted by the regression guard"
            attempts.setdefault(a["rule_id"], []).append(a)
        for rule_id, seq in attempts.items():
            row = rows[seq[0]["fix_type"]]
            row["instances"] += 1
            row["sent"] += len(seq)
            row["first"] += bool(seq[0]["verified"])
            within = any(a["verified"] for a in seq[:BUDGET])
            row["budget"] += within
            row["end"] += status.get(rule_id) == "pass"
            done = [a for a in seq if a["claimed"]]
            row["applied"] += len(done)
            row["applied_verified"] += sum(1 for a in done if a["verified"])
            if not within:
                why = cause(seq[0]["said"])
                row["causes"][why] = row["causes"].get(why, 0) + 1
        for r in (t.get("gate") or {}).get("review", []):
            if r["rule_id"] not in attempts and r["fix_type"] in rows:
                row = rows[r["fix_type"]]
                row["refused"] += 1
                reason = r["reason"].split(". Candidates")[0]
                row["refusals"][reason] = row["refusals"].get(reason, 0) + 1
    return rows


def share(part: int, whole: int) -> str:
    return f"{part} of {whole} ({part / whole:.1%})" if whole else "none"


def metrics(traces: list[dict]) -> str:
    """The measured tables for docs/metrics.md."""
    rows = measured(traces)
    out = ["| Fix type | Instances | Contracts sent | First attempt verified | Verified within budget "
           "| Passing at the end of the run | Applied as written, verified |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for kind in TYPES:
        r = rows[kind]
        out.append(f"| {kind} | {r['instances']} | {r['sent']} | {share(r['first'], r['instances'])} "
                   f"| {share(r['budget'], r['instances'])} | {share(r['end'], r['instances'])} "
                   f"| {share(r['applied_verified'], r['applied'])} |")
    out += ["", "| Fix type | Not verified within budget, because | Instances |", "| --- | --- | --- |"]
    for kind in TYPES:
        for why, n in sorted(rows[kind]["causes"].items(), key=lambda kv: -kv[1]):
            out.append(f"| {kind} | {why} | {n} |")
    p = rows["DYNAMIC-PARAMETRIC"]
    asked = p["instances"] + p["refused"]
    out += ["", f"DYNAMIC-PARAMETRIC ambiguity rate: {share(p['refused'], asked)} rule instances were "
                f"refused by extraction before any contract was sent.", "",
            "| Refused because | Instances |", "| --- | --- |"]
    for why, n in sorted(p["refusals"].items(), key=lambda kv: -kv[1]):
        out.append(f"| {why} | {n} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--markdown"]:
        sys.stdout.write(markdown(json.loads(Path(args[1]).read_text(encoding="utf-8"))))
    elif args[:1] == ["--metrics"]:
        sys.stdout.write(metrics(json.loads(Path(args[1]).read_text(encoding="utf-8"))))
    else:
        main(Path(args[0]).resolve(), args[1:])
