"""Evidence for the deployability model beyond a single held out split.

Module 5.4 reported the promoted model, D, on one stratified split. This module
adds what that report could not show on its own, all on the same data, the same
split and the same seed:

- compliance baselines, the audit score alone and the rule-only gate, which
  answer RQ4 without training a model on compliance labels;
- a build-only baseline, a logistic regression on the build result and the
  stack, which shows what the 25 audit features add beyond buildability;
- bootstrap confidence intervals on the held out rows and repeated cross
  validation over all rows;
- per-stack confusion matrices, the counterfactual clearing rate of fully fixed
  projects, and the sensitivity of decisions to the threshold and to the
  priority weights of the audit score;
- model E refitted with its recorded parameters, so the choice of D rests on
  more than one split.

The shipped model is read and never written. Every refit happens in memory.

    python -m prodpilot.modelstats data/revision docs/revision
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from prodpilot import features, labels, scoring, training
from prodpilot.rules import get_rule

logger = logging.getLogger(__name__)

RESAMPLES = 2000
REPEATS = 10
CUTS = (0.2, 0.3, 0.369, 0.45, 0.5)
STACKS = ("react_vite", "node_express")
PASS_MARK = 90
LEVELS = ("P0", "P1", "P2", "P3", "P4", "P5")
SCHEMES = {
    "halving": dict(zip(LEVELS, (32, 16, 8, 4, 2, 1))),
    "equal": dict(zip(LEVELS, (1, 1, 1, 1, 1, 1))),
    "linear": dict(zip(LEVELS, (6, 5, 4, 3, 2, 1))),
}
# Model E as module 5.4 recorded it in data/evaluation.json.
E_PARAMS = {"n_estimators": 100, "learning_rate": 0.1, "max_depth": 3,
            "min_samples_leaf": 1, "subsample": 0.8}


def rows() -> list[dict]:
    """The labelled feature rows, joined exactly as training.load joins them."""
    found = labels.read_rows(labels.LABELS)
    made = [labels.Label(str(r["name"]), str(r["kind"]), str(r.get("rule_id", "")),
                         str(r.get("commit", "")), int(r["label"]),
                         str(r.get("stage", "")), str(r.get("detail", "")))
            for r in found]
    return labels.join(labels.read_rows(features.MATRIX), made)


def split(y: list[int]) -> tuple[list[int], list[int]]:
    """Training and held out row indices, identical to training.run's split."""
    from sklearn.model_selection import train_test_split

    idx = list(range(len(y)))
    fit, test = train_test_split(idx, test_size=training.HELD_OUT,
                                 random_state=training.SEED, stratify=y)
    return fit, test


def measure(y: list[int], p: list[float], cut: float, prob: bool = True) -> dict:
    """Ranking, calibration and the decision at one cut, for one predictor."""
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    call = [1 if v >= cut else 0 for v in p]
    tp = sum(1 for a, b in zip(y, call) if a and b)
    fp = sum(1 for a, b in zip(y, call) if not a and b)
    fn = sum(1 for a, b in zip(y, call) if a and not b)
    tn = len(y) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    out = {"rows": len(y), "positive": sum(y), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "precision": precision, "recall": recall,
           "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    if len(set(y)) == 2:
        out["roc_auc"] = float(roc_auc_score(y, p))
        out["pr_auc"] = float(average_precision_score(y, p))
    if prob:
        out["brier"] = float(brier_score_loss(y, p))
    return out


def cells(idx: list[int], y: list[int], stack: list[str]) -> dict[tuple, list[int]]:
    """Held out rows grouped by label and stack, the strata of the bootstrap."""
    found: dict[tuple, list[int]] = defaultdict(list)
    for i in idx:
        found[(y[i], stack[i])].append(i)
    return found


def resample(groups: dict[tuple, list[int]], rng) -> list[int]:
    """One stratified bootstrap draw: every stratum keeps its size."""
    out: list[int] = []
    for members in groups.values():
        out.extend(members[j] for j in rng.integers(0, len(members), len(members)))
    return out


def interval(values: list[float]) -> dict[str, float]:
    import numpy as np

    if not values:
        return {}
    low, high = np.percentile(values, [2.5, 97.5])
    return {"low": round(float(low), 4), "high": round(float(high), 4)}


def rescore(scored: list, weights: dict[str, int]) -> int:
    """An audit score from (rule, priority, status) triples under other weights."""
    counted = [(p, s) for _, p, s in scored if s in ("pass", "fail")]
    available = sum(weights[p] for p, _ in counted)
    if not available:
        return 0
    return round(100 * sum(weights[p] for p, s in counted if s == "pass") / available)


def rounded(found):
    if isinstance(found, float):
        return round(found, 4)
    if isinstance(found, dict):
        return {k: rounded(v) for k, v in found.items()}
    if isinstance(found, list):
        return [rounded(v) for v in found]
    return found


def build_only():
    """Logistic regression on the build result and the stack, calibrated as D is."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression

    return CalibratedClassifierCV(LogisticRegression(), method=training.CALIBRATION,
                                  cv=training.FOLDS)


def model_e(seed: int):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier

    return CalibratedClassifierCV(GradientBoostingClassifier(random_state=seed, **E_PARAMS),
                                  method=training.CALIBRATION, cv=training.FOLDS)


def ahead(make, x_fit, y_fit, seed: int) -> list[float]:
    """Out of fold probabilities over the training rows, as training.run makes them."""
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    folds = StratifiedKFold(training.FOLDS, shuffle=True, random_state=seed)
    got = cross_val_predict(make(), x_fit, y_fit, cv=folds, method="predict_proba")
    return [float(r[1]) for r in got]


def run(raw: Path) -> dict:
    import numpy as np

    joined = rows()
    names = list(features.FEATURES)
    x = [list(r["values"]) for r in joined]
    y = [int(r["label"]) for r in joined]
    stack = [str(r["stack"]) for r in joined]
    fit, test = split(y)
    seed = training.SEED
    node, built = names.index("is_node"), names.index("built")
    fails = [i for i, n in enumerate(names) if n.startswith("failed_")]
    p0 = names.index("failed_p0")

    shipped = scoring.load()
    cut = float(shipped.threshold)
    logger.info("loaded model D, threshold %.4f", cut)

    # Every predictor as one score per row.
    scores: dict[str, list[float]] = {}
    scores["D"] = training.chances(shipped.model, x)
    scores["audit_score"] = [float(r["score"]) for r in joined]
    scores["rule_gate"] = [1.0 if row[p0] == 0 and r["score"] >= PASS_MARK else 0.0
                           for row, r in zip(x, joined)]
    x_fit, y_fit = [x[i] for i in fit], [y[i] for i in fit]
    pair = [[row[built], row[node]] for row in x]
    lr = build_only()
    lr.fit([pair[i] for i in fit], y_fit)
    scores["build_only"] = training.chances(lr, pair)
    e = model_e(seed)
    e.fit(x_fit, y_fit)
    scores["E"] = training.chances(e, x)

    cuts = {
        "D": cut,
        "audit_score": training.operating(y_fit, [scores["audit_score"][i] for i in fit]),
        "rule_gate": 0.5,
        "build_only": training.operating(y_fit, ahead(build_only, [pair[i] for i in fit],
                                                      y_fit, seed)),
        "E": training.operating(y_fit, ahead(lambda: model_e(seed), x_fit, y_fit, seed)),
    }
    probabilistic = {"D": True, "audit_score": False, "rule_gate": False,
                     "build_only": True, "E": True}

    def held(name: str, which: list[int]) -> dict:
        return measure([y[i] for i in which], [scores[name][i] for i in which],
                       cuts[name], probabilistic[name])

    by_stack = {s: [i for i in test if stack[i] == s] for s in STACKS}
    held_out = {name: {"overall": held(name, test),
                       **{s: held(name, by_stack[s]) for s in STACKS}} for name in scores}

    # Bootstrap on the held out rows, stratified by label and stack.
    rng = np.random.default_rng(seed)
    groups = cells(test, y, stack)
    keep = ("roc_auc", "pr_auc", "brier", "precision", "recall")
    draws: dict[str, dict[str, dict[str, list[float]]]] = {
        name: {part: defaultdict(list) for part in ("overall", *STACKS)} for name in scores}
    diff: dict[str, list[float]] = defaultdict(list)
    for _ in range(RESAMPLES):
        pick = resample(groups, rng)
        parts = {"overall": pick, **{s: [i for i in pick if stack[i] == s] for s in STACKS}}
        got = {}
        for name in scores:
            for part, which in parts.items():
                m = measure([y[i] for i in which], [scores[name][i] for i in which],
                            cuts[name], probabilistic[name])
                got[(name, part)] = m
                for k in keep:
                    if k in m:
                        draws[name][part][k].append(m[k])
        for k in ("roc_auc", "pr_auc"):
            if k in got[("D", "overall")] and k in got[("E", "overall")]:
                diff[k].append(got[("D", "overall")][k] - got[("E", "overall")][k])
    ci = {name: {part: {k: interval(v) for k, v in d.items()} for part, d in by.items()}
          for name, by in draws.items()}
    ci_diff = {k: {**interval(v), "mean": round(float(np.mean(v)), 4),
                   "share_above_zero": round(float(np.mean([d > 0 for d in v])), 4)}
               for k, v in diff.items()}

    # Repeated stratified five-fold cross validation over all rows.
    from sklearn.model_selection import RepeatedStratifiedKFold

    folds = RepeatedStratifiedKFold(n_splits=training.FOLDS, n_repeats=REPEATS, random_state=seed)
    repeated: dict[str, dict[str, list[float]]] = {n: defaultdict(list) for n in ("D", "E")}
    makers = {"D": lambda: training.estimator(seed), "E": lambda: model_e(seed)}
    for count, (tr, te) in enumerate(folds.split(x, y), 1):
        for name, make in makers.items():
            m = make()
            m.fit([x[i] for i in tr], [y[i] for i in tr])
            p = training.chances(m, [x[i] for i in te])
            got = measure([y[i] for i in te], p, cut if name == "D" else cuts["E"])
            for k in ("roc_auc", "pr_auc", "brier"):
                repeated[name][k].append(got[k])
            for s in STACKS:
                which = [j for j, i in enumerate(te) if stack[i] == s]
                sub = measure([y[te[j]] for j in which], [p[j] for j in which], cut)
                if "roc_auc" in sub:
                    repeated[name][f"{s}_roc_auc"].append(sub["roc_auc"])
                    repeated[name][f"{s}_pr_auc"].append(sub["pr_auc"])
        if count % 10 == 0:
            logger.info("repeated cross validation, fold %s of %s", count,
                        training.FOLDS * REPEATS)
    cv = {n: {k: {"mean": round(statistics.mean(v), 4), "sd": round(statistics.stdev(v), 4),
                  "folds": len(v)} for k, v in d.items()} for n, d in repeated.items()}

    # Every non deployer and deployer made fully fixed: failure counts set to zero.
    fixed_rows = []
    for row in x:
        moved = list(row)
        for i in fails:
            moved[i] = 0
        fixed_rows.append(moved)
    fixed_chance = training.chances(shipped.model, fixed_rows)

    def clearing(which: list[int], at: float) -> dict:
        out = {}
        for s in STACKS:
            for label, word in ((1, "deployers"), (0, "non_deployers")):
                members = [i for i in which if stack[i] == s and y[i] == label]
                out[f"{s}_{word}"] = {"clear": sum(1 for i in members if fixed_chance[i] >= at),
                                      "of": len(members)}
        return out

    counterfactual = {"all_rows": clearing(list(range(len(y))), cut),
                      "held_out": clearing(test, cut)}

    # The decision at other thresholds, and the F1 best cut within each stack.
    oof = ahead(lambda: training.estimator(seed), x_fit, y_fit, seed)
    reproduced = training.operating(y_fit, oof)
    best = {}
    for s in STACKS:
        which = [j for j, i in enumerate(fit) if stack[i] == s]
        best[s] = round(training.operating([y_fit[j] for j in which], [oof[j] for j in which]), 4)
    sweep = {}
    for at in CUTS:
        entry = {part: measure([y[i] for i in which], [scores["D"][i] for i in which], at)
                 for part, which in (("overall", test), *by_stack.items())}
        entry["fully_fixed_clearing"] = clearing(list(range(len(y))), at)
        sweep[str(at)] = entry

    # Audit score weights: flips of the pass mark under other schemes.
    weights = weight_check(raw, joined)

    return rounded({
        "rows": len(y), "positive": sum(y), "train": len(fit), "held_out": len(test),
        "held_out_by_stack": {s: {"rows": len(by_stack[s]),
                                  "positive": sum(y[i] for i in by_stack[s])} for s in STACKS},
        "threshold": cut, "threshold_reproduced": reproduced, "cuts": cuts,
        "held_out_metrics": held_out,
        "bootstrap": {"resamples": RESAMPLES, "strata": "label by stack", "ci": ci,
                      "d_minus_e": ci_diff},
        "repeated_cv": {"repeats": REPEATS, "folds": training.FOLDS, "models": cv},
        "fully_fixed": counterfactual,
        "threshold_sweep": sweep,
        "best_f1_threshold_by_stack": best,
        "weights": weights,
        "curves": curves(y, test, scores),
    })


def curves(y: list[int], test: list[int], scores: dict[str, list[float]]) -> dict:
    """Held out ROC and precision-recall curves for each predictor, for the figures."""
    from sklearn.metrics import precision_recall_curve, roc_curve

    truth = [y[i] for i in test]
    out = {}
    for name, got in scores.items():
        p = [got[i] for i in test]
        fpr, tpr, _ = roc_curve(truth, p)
        precision, recall, _ = precision_recall_curve(truth, p)
        out[name] = {"fpr": [float(v) for v in fpr], "tpr": [float(v) for v in tpr],
                     "precision": [float(v) for v in precision],
                     "recall": [float(v) for v in recall]}
    return out


def weight_check(raw: Path, joined: list[dict]) -> dict:
    """How many pass-mark decisions change under equal and linear weights."""
    found: dict[str, list] = {}
    for part in ("violations.jsonl", "negatives.jsonl"):
        path = raw / part
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if "scored" in r:
                    found[r["name"]] = r["scored"]

    def key(r: dict) -> str:
        # Negatives are already named after their directory, owner__repo_rule.
        return str(r["name"]).replace("/", "__")

    # Real repositories only. A synthetic negative's working copy carries no
    # Git history, so a fresh audit cannot read its history rules and would
    # differ from the audit the features were built with for that reason alone.
    real = [r for r in joined if r["kind"] == "repo"]
    matched = [(r, found[key(r)]) for r in real if key(r) in found]
    out = {"matrix_rows_matched": len(matched), "matrix_rows": len(real),
           "population": "real repositories in the model's matrix"}
    agree = sum(1 for r, s in matched if rescore(s, SCHEMES["halving"]) == r["score"])
    out["halving_reproduces_recorded_score"] = agree
    for scheme in ("equal", "linear"):
        flips = sum(1 for _, s in matched
                    if (rescore(s, SCHEMES["halving"]) >= PASS_MARK)
                    != (rescore(s, SCHEMES[scheme]) >= PASS_MARK))
        passing = sum(1 for _, s in matched if rescore(s, SCHEMES[scheme]) >= PASS_MARK)
        out[scheme] = {"flips_at_90": flips, "passing_at_90": passing}
    out["halving"] = {"passing_at_90": sum(1 for _, s in matched
                                           if rescore(s, SCHEMES["halving"]) >= PASS_MARK)}

    trace = raw / "pipeline_final.json"
    if trace.exists():
        runs = json.loads(trace.read_text(encoding="utf-8"))
        samples = {}
        for t in runs:
            status = (t.get("end") or {}).get("status") or {}
            scored = [[rid, get_rule(rid).priority.value, st] for rid, st in status.items()
                      if get_rule(rid) is not None]
            if scored:
                samples[t["sample"]] = {k: rescore(scored, w) for k, w in SCHEMES.items()}
        flips = {k: sorted(n for n, v in samples.items()
                           if (v["halving"] >= PASS_MARK) != (v[k] >= PASS_MARK))
                 for k in ("equal", "linear")}
        out["samples_at_gate"] = {"scored": len(samples), "scores": samples,
                                  "flips_at_90": {k: len(v) for k, v in flips.items()},
                                  "flipped": flips}
    return out


def markdown(s: dict) -> str:
    def f(v) -> str:
        return "n/a" if v is None else f"{v:.3f}"

    def band(ci: dict, k: str) -> str:
        c = ci.get(k) or {}
        return f"{c['low']:.3f} to {c['high']:.3f}" if c else "n/a"

    names = {"D": "Model D (promoted)", "E": "Model E", "build_only": "Build and stack only",
             "audit_score": "Audit score alone", "rule_gate": "Rule-only gate"}
    lines = ["# Deployability model: baselines, uncertainty and sensitivity", "",
             "Produced by `python -m prodpilot.modelstats`. Same 675 rows, same split",
             f"({s['train']} training, {s['held_out']} held out) and seed as module 5.4. "
             f"Threshold {s['threshold']}, reproduced from out-of-fold predictions as "
             f"{s['threshold_reproduced']}.", "",
             "## Held out rows, with 95% bootstrap intervals", "",
             "| Predictor | Cut | ROC AUC | 95% CI | PR AUC | 95% CI | Precision | Recall |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k, label in names.items():
        m = s["held_out_metrics"][k]["overall"]
        ci = s["bootstrap"]["ci"][k]["overall"]
        lines.append(f"| {label} | {s['cuts'][k]:.3f} | {f(m.get('roc_auc'))} | {band(ci, 'roc_auc')} | "
                     f"{f(m.get('pr_auc'))} | {band(ci, 'pr_auc')} | {f(m['precision'])} | "
                     f"{f(m['recall'])} |")
    for st in STACKS:
        lines += ["", f"### {st}", "",
                  "| Predictor | ROC AUC | 95% CI | PR AUC | 95% CI | Precision | Recall | tn fp fn tp |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for k, label in names.items():
            m = s["held_out_metrics"][k][st]
            ci = s["bootstrap"]["ci"][k][st]
            lines.append(f"| {label} | {f(m.get('roc_auc'))} | {band(ci, 'roc_auc')} | "
                         f"{f(m.get('pr_auc'))} | {band(ci, 'pr_auc')} | {f(m['precision'])} | "
                         f"{f(m['recall'])} | {m['tn']} {m['fp']} {m['fn']} {m['tp']} |")
    d = s["bootstrap"]["d_minus_e"]
    lines += ["", "## Model D against model E, paired bootstrap", "",
              "| Measure | Mean D minus E | 95% CI | Share of draws where D is higher |",
              "| --- | --- | --- | --- |"]
    lines += [f"| {k} | {v['mean']:.3f} | {v['low']:.3f} to {v['high']:.3f} | "
              f"{v['share_above_zero']:.3f} |" for k, v in d.items()]
    lines += ["", f"## Repeated cross validation, {s['repeated_cv']['repeats']} repeats of "
              f"{s['repeated_cv']['folds']} folds over all rows", "",
              "| Model | Measure | Mean | SD |", "| --- | --- | --- | --- |"]
    for n, by in s["repeated_cv"]["models"].items():
        lines += [f"| {n} | {k} | {v['mean']:.3f} | {v['sd']:.3f} |" for k, v in by.items()]
    lines += ["", "## Fully fixed projects clearing the threshold", "",
              "| Group | All rows | Held out |", "| --- | --- | --- |"]
    for k in s["fully_fixed"]["all_rows"]:
        a, h = s["fully_fixed"]["all_rows"][k], s["fully_fixed"]["held_out"][k]
        lines.append(f"| {k} | {a['clear']} of {a['of']} | {h['clear']} of {h['of']} |")
    lines += ["", "## Threshold sensitivity, model D on held out rows", "",
              "| Threshold | Precision | Recall | Express precision | Express recall | React precision | React recall |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for at, e in s["threshold_sweep"].items():
        lines.append(f"| {at} | {e['overall']['precision']:.3f} | {e['overall']['recall']:.3f} | "
                     f"{e['node_express']['precision']:.3f} | {e['node_express']['recall']:.3f} | "
                     f"{e['react_vite']['precision']:.3f} | {e['react_vite']['recall']:.3f} |")
    lines += ["", "F1-best threshold within each stack, from out-of-fold predictions on the "
              "training rows (information only): " +
              ", ".join(f"{k} {v}" for k, v in s["best_f1_threshold_by_stack"].items()) + "."]
    w = s["weights"]
    lines += ["", "## Priority weights of the audit score", "",
              f"Real repositories in the matrix matched to their audit record: "
              f"{w['matrix_rows_matched']} of {w['matrix_rows']}; the released audit reproduces "
              f"the score stored with the features on {w['halving_reproduces_recorded_score']}. "
              "Synthetic negatives are excluded: their working copies carry no Git history.", "",
              "| Scheme | Rows passing 90 | Decisions that change |", "| --- | --- | --- |",
              f"| halving (32 to 1, in use) | {w['halving']['passing_at_90']} | |",
              f"| equal | {w['equal']['passing_at_90']} | {w['equal']['flips_at_90']} |",
              f"| linear (6 to 1) | {w['linear']['passing_at_90']} | {w['linear']['flips_at_90']} |"]
    if "samples_at_gate" in w:
        g = w["samples_at_gate"]
        lines += ["", f"The {g['scored']} scored samples at the gate: equal weights change "
                  f"{g['flips_at_90']['equal']} pass-mark decisions "
                  f"({', '.join(g['flipped']['equal']) or 'none'}), linear weights change "
                  f"{g['flips_at_90']['linear']} ({', '.join(g['flipped']['linear']) or 'none'})."]
    return "\n".join(lines) + "\n"


def main(raw: str, out: str) -> dict:
    found = run(Path(raw))
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "model.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    (target / "model.md").write_text(markdown(found), encoding="utf-8")
    return found


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main(sys.argv[1] if len(sys.argv) > 1 else "data/revision",
         sys.argv[2] if len(sys.argv) > 2 else "docs/revision")
