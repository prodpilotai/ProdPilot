"""Behaviour baseline for every committed sample project.

Records, for each project under tests/samples, what 1.0.0 decided about it: the
detected stack, the score and band, every rule's verdict, the fix contract each
failing rule produces, and the gate's decision. Later releases must reproduce
this record exactly, except for differences a module documents in
tests/baseline/allowed.json. Each documented difference names the rules whose
fix contracts it touches and the exact text substitutions it makes. The
substitutions are applied to the stored record before comparing, so anything
else that changed in those contracts is still reported.

The gate asks whether the project builds before it consults the model, and that
check runs Render's build command in Docker. A baseline that needs Docker cannot
run on every CI runner, and a build that depends on the npm registry is not
deterministic. So the gate is evaluated twice per project, once as if the build
succeeded and once as if it failed, which records every decision the gate can
reach for that project without running a build.

Usage:
    python tests/regress.py           compare against the stored baseline
    python tests/regress.py --write   write the baseline (only from 1.0.0)
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from prodpilot import audit, builds, dispatch, gate, reaudit
from prodpilot.server import FIX_ERRORS

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
BASELINE = HERE / "baseline" / "v1_0_0.json"
ALLOWED = HERE / "baseline" / "allowed.json"


def scrub(value, root: Path):
    """Replace the absolute sample path, so the record is the same on any machine."""
    if isinstance(value, dict):
        return {k: scrub(v, root) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, root) for v in value]
    if isinstance(value, str):
        for form in {str(root), root.as_posix(), str(root.resolve()), root.resolve().as_posix()}:
            value = value.replace(form, "<root>")
        return value
    return value


@contextmanager
def built_as(outcome: str):
    """Answer the gate's build question with a fixed outcome."""
    held = builds.cached

    def cached(root, stack, run=None):
        return builds.Build(outcome, reason="fixed by the behaviour baseline")

    builds.cached = cached
    try:
        yield
    finally:
        builds.cached = held


def record(root: Path) -> dict:
    """Everything 1.0.0 decides about one project, without writing to it."""
    report = audit.run(root)
    contracts: dict[str, dict] = {}
    for issue in report.issues:
        try:
            fix = dispatch.instruct(root, issue)
        except FIX_ERRORS as exc:
            contracts[issue.rule_id] = {"ok": False, "error": str(exc)}
        else:
            contracts[issue.rule_id] = {"ok": True, "fix": fix.to_dict()}

    decisions: dict[str, dict] = {}
    for outcome in (builds.BUILT, builds.FAILED):
        with built_as(outcome):
            result = reaudit.after(root)
            ready, reason = gate.clears(result)
            chance, operating = gate.estimate(result) if result.ok else (None, None)
        decisions[outcome] = {
            "ready": ready,
            "reason": reason,
            "chance": round(chance, 6) if chance is not None else None,
            "operating": round(operating, 6) if operating is not None else None,
        }

    return scrub({
        "stack": report.stack.value,
        "detection": report.detection.to_dict(),
        "score": report.score,
        "band": report.band.value,
        "rules": {r.rule_id: r.status.value for r in report.results},
        "blockers": [r.rule_id for r in report.blockers],
        "contracts": contracts,
        "gate": decisions,
    }, root)


def snapshot() -> dict:
    names = sorted(p.name for p in SAMPLES.iterdir() if p.is_dir())
    return {name: record(SAMPLES / name) for name in names}


def differences(stored: dict, current: dict, prefix: str = "") -> list[str]:
    """Every path at which two records disagree, as readable lines."""
    out: list[str] = []
    if isinstance(stored, dict) and isinstance(current, dict):
        for key in sorted(set(stored) | set(current)):
            where = f"{prefix}/{key}"
            if key not in current:
                out.append(f"{where}: missing now")
            elif key not in stored:
                out.append(f"{where}: new, not in the baseline")
            else:
                out.extend(differences(stored[key], current[key], where))
    elif stored != current:
        out.append(f"{prefix}: {json.dumps(stored)[:200]} -> {json.dumps(current)[:200]}")
    return out


def allowed() -> list[dict]:
    """Documented differences, each with its reason, rules and substitutions."""
    if not ALLOWED.is_file():
        return []
    return json.loads(ALLOWED.read_text(encoding="utf-8"))


def _replaced(value, pairs: list[list[str]]):
    if isinstance(value, dict):
        return {k: _replaced(v, pairs) for k, v in value.items()}
    if isinstance(value, list):
        return [_replaced(v, pairs) for v in value]
    if isinstance(value, str):
        for old, new in pairs:
            value = value.replace(old, new)
    return value


def expected(stored: dict, allow: list[dict]) -> dict:
    """The stored record with every documented substitution applied to its contracts."""
    out = json.loads(json.dumps(stored))
    for entry in allow:
        for name in entry["projects"]:
            project = out[name]
            for rule in entry["rules"]:
                if rule in project["contracts"]:
                    project["contracts"][rule] = _replaced(project["contracts"][rule],
                                                           entry["replace"])
    return out


def main(argv: list[str]) -> int:
    current = snapshot()
    if "--write" in argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"wrote {BASELINE} for {len(current)} projects")
        return 0
    stored = json.loads(BASELINE.read_text(encoding="utf-8"))
    raw = differences(stored, current)
    for line in raw:
        print(f"differs from 1.0.0: {line}")
    allow = allowed()
    bad = differences(expected(stored, allow), current)
    for line in bad:
        print(f"UNEXPLAINED: {line}")
    print(f"{len(current)} projects, {len(raw)} difference(s) from 1.0.0, "
          f"{len(bad)} not explained by {ALLOWED.name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
