"""Assemble the artefact bundle that goes with the paper, in a folder outside the repository.

The bundle holds what a reader needs to rerun every number: the dataset
manifest with each row's label, build result and split, the feature matrix,
the frozen ruleset, the promoted model, the committed samples, the analysis
scripts and their outputs. It holds no third-party source code: the
repositories are listed by name, commit and licence, and can be fetched again.

    python tests/bundle.py OUT_DIR
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCRIPTS = ("src/prodpilot/violations.py", "src/prodpilot/modelstats.py",
           "src/prodpilot/labelstats.py", "tests/facts.py", "tests/timing.py",
           "tests/agents.py", "tests/figures.py", "tests/pipeline.py", "tests/bundle.py")
README = """# ProdPilot artefact bundle

Everything needed to rerun the numbers in the paper, from ProdPilot {version}
at commit {commit}.

- manifest.csv: every labelled project with its repository URL, commit,
  licence, stack, kind, label, build result and split.
- features.jsonl and features.names.json: the 26-feature matrix.
- ruleset.json: the frozen 50 rules with their domain, priority and fix type.
- model/: the promoted model and the scikit-learn version that saved it.
- samples/: the 22 committed sample projects. Some contain credential-shaped
  strings, such as a token prefixed ghp_ or rnd_, that exercise the secret
  rules; they are fabricated and belong to no account.
- scripts/: the analysis scripts, and results/: their outputs.

To rerun, install ProdPilot at the commit above and, from its repository root:

    git clone each repository in manifest.csv at its commit into data/cache
    python -m prodpilot.violations data/revision/violations.jsonl docs/revision
    python -m prodpilot.modelstats data/revision docs/revision
    python -m prodpilot.labelstats data/revision docs/revision
    python tests/facts.py docs/revision
    python tests/timing.py docs/revision
    python tests/figures.py docs/revision docs/revision/figures

The labels themselves came from deploying each project to Render, which this
bundle does not repeat; they are recorded in manifest.csv.
"""


def git(*args: str) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def manifest(out: Path) -> int:
    from prodpilot import modelstats

    kept = {r["name"]: r["entry"] for r in read(DATA / "repos.jsonl") if r.get("outcome") == "kept"}
    builds = {(r["name"], r["kind"], r.get("rule_id", "")): r for r in read(DATA / "builds.jsonl")}
    joined = modelstats.rows()
    fit, test = modelstats.split([int(r["label"]) for r in joined])
    split = {}
    for part, idx in (("train", fit), ("held_out", test)):
        for i in idx:
            r = joined[i]
            split[(r["name"], r["kind"], r.get("rule_id", ""))] = part
    rows = 0
    with (out / "manifest.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "kind", "broken_rule", "url", "commit", "licence", "stack", "label",
                    "built", "split"])
        for lab in read(DATA / "labels.jsonl"):
            key = (lab["name"], lab["kind"], lab.get("rule_id", ""))
            source = lab["name"]
            if lab["kind"] == "negative":
                from prodpilot.labelstats import origin
                source = origin(lab["name"], lab.get("rule_id", ""))
            entry = kept.get(source, {})
            b = builds.get(key, {})
            w.writerow([lab["name"], lab["kind"], lab.get("rule_id", ""), entry.get("url", ""),
                        lab.get("commit") or entry.get("commit", ""), entry.get("license", ""),
                        b.get("stack") or entry.get("stack", ""), lab["label"],
                        b.get("built", ""), split.get(key, "not in matrix")])
            rows += 1
    return rows


def ruleset(out: Path) -> int:
    from prodpilot.rules import ALL_RULES

    rules = [{"rule": r.rule_id, "stack": r.stack.value, "domain": r.domain.value,
              "priority": r.priority.value, "scope": r.scope.value, "check": r.check_type.value,
              "fix_type": r.fix_type.value, "description": r.description} for r in ALL_RULES]
    (out / "ruleset.json").write_text(json.dumps(rules, indent=1) + "\n", encoding="utf-8")
    return len(rules)


def main(dst: str) -> None:
    import sklearn

    out = Path(dst)
    if out.exists():
        raise SystemExit(f"{out} exists; remove it first")
    out.mkdir(parents=True)
    rows = manifest(out)
    count = ruleset(out)
    for name in ("features.jsonl", "features.names.json"):
        shutil.copy2(DATA / name, out / name)
    (out / "model").mkdir()
    shutil.copy2(ROOT / "src" / "prodpilot" / "model" / "model.joblib", out / "model" / "model.joblib")
    (out / "model" / "VERSION.txt").write_text(f"scikit-learn {sklearn.__version__}\n", encoding="utf-8")
    shutil.copytree(ROOT / "tests" / "samples", out / "samples")
    (out / "scripts").mkdir()
    for rel in SCRIPTS:
        shutil.copy2(ROOT / rel, out / "scripts" / Path(rel).name)
    shutil.copytree(ROOT / "docs" / "revision", out / "results")
    version = git("describe", "--tags", "--always")
    (out / "README.md").write_text(README.format(version=version, commit=git("rev-parse", "HEAD")),
                                   encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{out}: manifest {rows} rows, {count} rules, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "E:/tmp/prodpilot-bundle")
