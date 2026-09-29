"""How long the audit and one repair cycle take on each committed sample.

The whole chain was timed per sample by tests/pipeline.py. This splits out the
two stages that need no Docker daemon and no external service: the audit, taken
as the median of three runs, and one cycle of the repair loop with the stand-in
executor, which includes the regression guard's full audit after every change.
The gate's build and ProdPush stage 3 need Docker and are reported only inside
the recorded whole-chain times.

    python tests/timing.py docs/revision [data/revision/pipeline_final.json]
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pipeline import SAMPLES, prepare  # noqa: E402
from apply import applier  # noqa: E402
from prodpilot import audit  # noqa: E402
from prodpilot.dispatch import Fixer  # noqa: E402
from prodpilot.loop import Loop  # noqa: E402
from prodpilot.verify import verifier  # noqa: E402

RUNS = 3


def spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {"samples": len(values), "median": round(statistics.median(values), 2),
            "min": round(min(values), 2), "max": round(max(values), 2)}


def measure(name: str, work: Path) -> dict:
    root, _ = prepare(name, work)
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        try:
            report = audit.run(root)
        except Exception as exc:
            return {"sample": name, "refused": f"{type(exc).__name__}: {exc}"}
        times.append(time.perf_counter() - start)
    record = {"sample": name, "audit_seconds": round(statistics.median(times), 3),
              "issues": len(report.issues)}
    if report.issues:
        fixer = Fixer(root, applier(root), verifier(root))
        start = time.perf_counter()
        Loop(fixer.resolve, lambda cycle: (), ceiling=1).run(report.issues)
        record["cycle_seconds"] = round(time.perf_counter() - start, 3)
        record["contracts"] = len(fixer.claims)
    return record


def main(out: str, trace: str | None) -> dict:
    names = sorted(p.name for p in SAMPLES.iterdir() if p.is_dir())
    rows = []
    for name in names:
        work = Path(tempfile.mkdtemp(prefix="timing-"))
        try:
            rows.append(measure(name, work))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    chain = []
    if trace and Path(trace).exists():
        chain = [t["seconds"] for t in json.loads(Path(trace).read_text(encoding="utf-8"))]
    found = {"samples": rows,
             "audit": spread([r["audit_seconds"] for r in rows if "audit_seconds" in r]),
             "cycle": spread([r["cycle_seconds"] for r in rows if "cycle_seconds" in r]),
             "whole_chain_recorded": spread(chain),
             "note": "Gate build and ProdPush stage 3 need Docker; they are inside the "
                     "whole-chain times recorded by tests/pipeline.py."}
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "timing.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    lines = ["# Run time per stage", "", "Produced by `python tests/timing.py`.", "",
             "| Stage | Samples | Median s | Min s | Max s |", "| --- | --- | --- | --- | --- |"]
    for key, label in (("audit", "Audit"), ("cycle", "One repair cycle, with the regression guard"),
                       ("whole_chain_recorded", "Whole chain, recorded run")):
        s = found[key]
        if s:
            lines.append(f"| {label} | {s['samples']} | {s['median']} | {s['min']} | {s['max']} |")
    lines += ["", found["note"], "", "| Sample | Audit s | Failing rules | Cycle s | Contracts |",
              "| --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['sample']} | {r.get('audit_seconds', r.get('refused', ''))} | "
                     f"{r.get('issues', '')} | {r.get('cycle_seconds', '')} | {r.get('contracts', '')} |")
    (target / "timing.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return found


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "docs/revision",
                          sys.argv[2] if len(sys.argv) > 2 else "data/revision/pipeline_final.json"),
                     indent=1)[:1500])
