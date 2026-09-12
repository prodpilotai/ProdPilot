"""Training and serialisation, Phase 5 module 5.4.

Scope is turning module 5.2's feature matrix and module 5.3's real labels into
one serialised model that module 5.5 can load and the scoring gate can read.

Section 6 names the estimator: a GradientBoostingClassifier, trained offline,
serialised with joblib, with feature importances reported. This module uses its
histogram based sibling, HistGradientBoostingClassifier, and that is a
deliberate deviation made on evidence, not a swap of convenience. It is wrapped
in scikit-learn's sigmoid calibration, because the gate reads its output as a
probability, and a probability the gate acts on has to mean what it says.

Why the estimator changed
-------------------------
Trained on the audit's 25 features, the model could move the wrong way as a
project was fixed. With every failing rule cleared, React projects that deployed
and React projects that did not both fell to the same estimate, 0.138, and the
react_vite_ready sample, audit score 99 with no critical failure, was estimated
at 0.055 while react_vite_app, audit score 29, was estimated at 0.373. A model
the gate consults after the fix loop must never say that fixing a rule made a
project less likely to deploy.

A monotonic constraint rules that out by construction. scikit-learn supports
one on HistGradientBoostingClassifier through monotonic_cst, and in the
installed version, 1.9.0, GradientBoostingClassifier has no such parameter. So
the estimator changes to the one that can carry the constraint. It is the same
family, gradient boosted decision trees fitted to the same log loss.

The constraint, and how its direction is known
-----------------------------------------------
scikit-learn documents that, for binary classification, the constraint holds
over the probability of the positive class, and here the positive class is a
project that deployed. So every failed_ count is constrained with -1: more
failures can only lower the estimate or leave it alone, which is the same as
saying that clearing one can never lower it. built is constrained with +1: a
build that succeeds can never lower it. Every other feature is left free.

The direction is not taken on trust. After training, run lowers each failure
count by one and turns each failed build into a successful one, on every row of
the dataset, and refuses to return the model if any estimate went down by more
than rounding. Sigmoid calibration maps each fold's decision function through a
fitted logistic curve, which preserves the order only when the curve's slope
comes out positive, so this check is what proves the constraint survived
calibration for the model actually fitted.

How the configuration is chosen
--------------------------------
Every choice is made by cross validation on the training rows only, and the held
out rows are used once, to report the model that was chosen.

The 26 feature matrix holds 675 labelled rows: the 684 module 5.3 labelled,
less 9 whose build could not be determined. Its stratified split with the fixed
seed leaves 506 rows for training and 169 held out, 32 of them deployers.

The values in PARAMS come from a grid search of 108 combinations over learning
rate, iterations, leaf count, minimum leaf size and L2 regularisation, scored
by 5 fold cross validated PR AUC on the training rows with the constraint on.
The chosen values scored 0.731, with a spread of 0.054 between folds, against
0.722 and 0.039 for scikit-learn's defaults. That is inside the spread, so the
honest reading is again that the defaults were close to right; the chosen
values are kept because the procedure chose them.

Why the imbalance is handled at the threshold, not with weights
-----------------------------------------------------------------
An earlier version weighted each row by the inverse frequency of its class.
Measured on the real dataset, that bought nothing in ranking and inflated every
probability the gate reads. So the model is trained on the data as it is and
calibrated to the real deploy rate, and the imbalance is handled where it
belongs, in the decision threshold.

That threshold is the operating point that maximises F1 on out of fold
predictions over the training rows. It is stored in the artifact, because a
calibrated model on a dataset that is 19 percent positive rarely goes above 0.5.

Why the split is stratified
----------------------------
The positive class in this dataset is rare. A plain random split can put every
positive row on one side, which makes the held out metrics meaningless. A
stratified split keeps the same class balance in both halves. Twenty five
percent is held out, and the seed is fixed so a rerun produces the same split
and the same numbers.

What the report measures against
---------------------------------
Accuracy is reported but never led with. Beside the model's ranking scores the
report gives the PR AUC of guessing from the stack alone, because the two stacks
deploy at very different rates. Every held out measure is also reported for each
stack on its own, where the stack itself tells the model nothing, together with
a calibration table of predicted against observed deploy rates.

Importances are permutation importances on the held out rows, since
HistGradientBoostingClassifier reports no impurity importances: how much PR AUC
falls when one feature's values are shuffled, averaged over repeated shuffles,
with the spread beside the mean.

Why the metadata travels with the model
----------------------------------------
Module 5.5 has to build a feature vector in exactly the order this model was
trained on and judge the estimate against the threshold chosen here, and a
reader has to know when it was trained, how, and how well it did. All of that
is written into the artifact beside the estimator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from prodpilot import features, labels

logger = logging.getLogger(__name__)

DATA = Path("data")
ARTIFACT = DATA / "model.joblib"

# Held out for evaluation.
HELD_OUT = 0.25

# Fixed so a rerun reproduces the split, the model and the reported numbers.
SEED = 20260910

# Below this there is nothing to evaluate honestly.
FLOOR = 20

# Training cross validates twice, once to calibrate and once to choose the
# threshold. With fewer rows of the rarer class than this, some folds would hold
# none of it.
MINORITY = 10

# Chosen by grid search on the training rows of the 26 feature matrix, with the
# constraint on. The module docstring says how, and how little it mattered.
# early_stopping is always off, so every fit uses max_iter trees and a rerun
# reproduces the model exactly.
PARAMS = {"learning_rate": 0.05, "max_iter": 200, "max_leaf_nodes": 7,
          "min_samples_leaf": 20, "l2_regularization": 1.0}

# Sigmoid rather than isotonic. Isotonic regression needs more data than this to
# be trusted.
CALIBRATION = "sigmoid"
FOLDS = 5

# How many times each feature is shuffled for its permutation importance.
REPEATS = 30

# The largest fall in an estimate accepted as floating point rounding when the
# constraint is checked. Anything larger is a violation and the model is refused.
SLACK = 1e-9

# Edges of the calibration table, denser at the low end where most estimates are.
EDGES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.6, 1.0)


class TrainError(Exception):
    """Raised when a model cannot be trained or serialised honestly."""


@dataclass(frozen=True)
class Metrics:
    """How the model did on rows it never saw.

    The four confusion matrix cells are kept, not just the ratios, because on a
    small imbalanced test set the raw counts are what a reader needs to judge
    whether a ratio means anything. They are counted at the stored threshold,
    which is the decision the gate actually makes.
    """

    accuracy: float
    precision: float
    recall: float
    f1: float
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int
    roc_auc: float = 0.0
    pr_auc: float = 0.0
    brier: float = 0.0
    threshold: float = 0.5
    baseline: float = 0.0
    within: dict[str, float] = field(default_factory=dict)
    by_stack: dict[str, dict] = field(default_factory=dict)
    reliability: tuple[tuple[float, float, int, float, float], ...] = ()

    @property
    def tested(self) -> int:
        return (self.true_negative + self.false_positive
                + self.false_negative + self.true_positive)

    def to_dict(self) -> dict[str, object]:
        return {
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "roc_auc": round(self.roc_auc, 4),
            "pr_auc": round(self.pr_auc, 4),
            "brier": round(self.brier, 4),
            "threshold": round(self.threshold, 4),
            "baseline_pr_auc": round(self.baseline, 4),
            "within_stack_roc_auc": {k: round(v, 4) for k, v in self.within.items()},
            "by_stack": self.by_stack,
            "reliability": [list(row) for row in self.reliability],
            "confusion": {
                "true_negative": self.true_negative,
                "false_positive": self.false_positive,
                "false_negative": self.false_negative,
                "true_positive": self.true_positive,
            },
            "tested": self.tested,
        }

    def report(self) -> str:
        lines = [
            f"  ROC AUC    {self.roc_auc:.3f}   ranking quality, 0.5 is chance",
            f"  PR AUC     {self.pr_auc:.3f}   ranking of the rarer class, "
            f"guessing from the stack alone scores {self.baseline:.3f}",
            f"  Brier      {self.brier:.3f}   how honest the probabilities are, "
            f"lower is better",
            "",
            f"  at the operating threshold of {self.threshold:.3f}, "
            f"chosen on the training rows",
            f"  accuracy   {self.accuracy:.3f}",
            f"  precision  {self.precision:.3f}",
            f"  recall     {self.recall:.3f}",
            f"  f1         {self.f1:.3f}",
            "",
            "  confusion matrix, rows are truth and columns are prediction",
            f"                 predicted 0   predicted 1",
            f"    actual 0     {self.true_negative:>11}   {self.false_positive:>11}",
            f"    actual 1     {self.false_negative:>11}   {self.true_positive:>11}",
        ]
        if self.within:
            lines.append("")
            lines.append("  ROC AUC within each stack, where the stack tells the "
                         "model nothing")
            for stack, value in self.within.items():
                lines.append(f"    {stack:<14} {value:.3f}")
        if self.by_stack:
            lines.append("")
            lines.append("  every held out measure for each stack on its own")
            for stack, found in self.by_stack.items():
                cells = found["confusion"]
                lines.append(
                    f"    {stack:<14} {found['rows']} rows, {found['positive']} deployed; "
                    f"ROC AUC {found.get('roc_auc', float('nan')):.3f}, "
                    f"PR AUC {found.get('pr_auc', float('nan')):.3f}, "
                    f"Brier {found['brier']:.3f}; precision {found['precision']:.3f}, "
                    f"recall {found['recall']:.3f}; "
                    f"tn {cells['tn']} fp {cells['fp']} fn {cells['fn']} tp {cells['tp']}")
        if self.reliability:
            lines.append("")
            lines.append("  calibration, predicted against observed deploy rate")
            for low, high, rows, said, seen in self.reliability:
                lines.append(f"    {low:.1f} to {high:.1f}   {rows:>4} rows   "
                             f"predicted {said:.3f}   observed {seen:.3f}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Trained:
    """The artifact, and everything module 5.5 needs to use it safely."""

    model: object
    names: tuple[str, ...]
    trained: str
    metrics: Metrics
    rows: int
    positive: int
    negative: int
    params: dict[str, object] = field(default_factory=dict)
    calibration: str = ""
    ranked: tuple[tuple[str, float, float], ...] = ()
    monotone: dict[str, object] = field(default_factory=dict)

    @property
    def threshold(self) -> float:
        return self.metrics.threshold

    @property
    def balance(self) -> float:
        """The share of rows that are positive."""
        return self.positive / self.rows if self.rows else 0.0

    @property
    def imbalanced(self) -> bool:
        """Whether the class balance is far enough off to distrust accuracy."""
        return not (0.3 <= self.balance <= 0.7)

    def to_dict(self) -> dict[str, object]:
        """Everything except the estimator itself, for a report or a file."""
        return {
            "names": list(self.names),
            "trained": self.trained,
            "rows": self.rows,
            "positive": self.positive,
            "negative": self.negative,
            "balance": round(self.balance, 4),
            "imbalanced": self.imbalanced,
            "threshold": round(self.threshold, 4),
            "params": dict(self.params),
            "calibration": self.calibration,
            "constraints": constraints(self.names),
            "monotone": dict(self.monotone),
            "importances": [[n, round(m, 4), round(s, 4)] for n, m, s in self.ranked],
            "metrics": self.metrics.to_dict(),
        }


def load(matrix: str | Path = features.MATRIX,
         outcomes: str | Path = labels.LABELS) -> tuple[list, list, tuple[str, ...]]:
    """Module 5.2's features joined to module 5.3's labels.

    The join is module 5.3's own, so the identity rule lives in one place.
    """
    rows = labels.read_rows(matrix)
    if not rows:
        raise TrainError(f"{matrix} holds no feature rows")

    found = labels.read_rows(outcomes)
    if not found:
        raise TrainError(f"{outcomes} holds no labels, run module 5.3 first")

    made = [labels.Label(str(r["name"]), str(r["kind"]), str(r.get("rule_id", "")),
                         str(r.get("commit", "")), int(r["label"]),
                         str(r.get("stage", "")), str(r.get("detail", "")))
            for r in found]

    joined = labels.join(rows, made)
    if not joined:
        raise TrainError("no feature row matched a label, the join produced nothing")

    names = tuple(features.FEATURES)
    x = [list(r["values"]) for r in joined]
    y = [int(r["label"]) for r in joined]

    wrong = {len(v) for v in x} - {len(names)}
    if wrong:
        raise TrainError(f"a feature row has {wrong} values, expected {len(names)}")

    logger.info("loaded %s labelled rows, %s positive", len(y), sum(y))
    return x, y, names


def check(y: list[int]) -> None:
    """Refuse a dataset that cannot produce an honest model."""
    kinds = set(y)
    if len(kinds) < 2:
        only = kinds.pop() if kinds else "none"
        raise TrainError(
            f"every label is {only}, so there is nothing to learn. A classifier "
            f"needs both outcomes present. Label more rows with module 5.3.")
    if len(y) < FLOOR:
        raise TrainError(
            f"{len(y)} labelled row(s) is too few to evaluate honestly, "
            f"at least {FLOOR} are needed")
    fewest = min(y.count(0), y.count(1))
    if fewest < MINORITY:
        raise TrainError(
            f"only {fewest} row(s) carry the rarer class, and at least {MINORITY} "
            f"are needed to split, calibrate and choose a threshold honestly")


def constraints(names=features.FEATURES) -> list[int]:
    """The monotonic constraint on each feature, by name and in order.

    -1 on every failure count, +1 on the build result, 0 on everything else,
    for the reasons in the module docstring.
    """
    return [-1 if name.startswith("failed_") else 1 if name == "built" else 0
            for name in names]


def estimator(seed: int = SEED, loud: int = 0):
    """The model: a monotonically constrained HistGradientBoostingClassifier,
    inside sigmoid calibration."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier

    return CalibratedClassifierCV(
        HistGradientBoostingClassifier(random_state=seed, verbose=loud,
                                       early_stopping=False,
                                       monotonic_cst=constraints(), **PARAMS),
        method=CALIBRATION, cv=FOLDS)


def chances(model, x) -> list[float]:
    """The probability of the positive class, found by label not by position."""
    found = model.predict_proba(x)
    column = list(model.classes_).index(1)
    return [float(row[column]) for row in found]


def operating(y: list[int], chance: list[float]) -> float:
    """The threshold that maximises F1 over one set of predictions."""
    from sklearn.metrics import precision_recall_curve

    precision, recall, cuts = precision_recall_curve(y, chance)
    best, cut = -1.0, 0.5
    for p, r, c in zip(precision[:-1], recall[:-1], cuts):
        score = 2 * p * r / (p + r) if p + r else 0.0
        if score > best:
            best, cut = score, float(c)
    return cut


def monotone(model, x, names) -> dict[str, object]:
    """Check the constraint on a fitted model, one feature at a time.

    Every row with a failure count above zero has it lowered by one, and every
    row whose build failed has it set to succeeded. Neither is allowed to lower
    the estimate by more than SLACK.
    """
    base = chances(model, x)
    checked = violations = 0
    worst = 0.0
    for index, name in enumerate(names):
        if name.startswith("failed_"):
            rows = [r for r, row in enumerate(x) if row[index] > 0]
            moved = [list(x[r]) for r in rows]
            for row in moved:
                row[index] -= 1
        elif name == "built":
            rows = [r for r, row in enumerate(x) if row[index] == 0]
            moved = [list(x[r]) for r in rows]
            for row in moved:
                row[index] = 1
        else:
            continue
        if not rows:
            continue
        for r, after in zip(rows, chances(model, moved)):
            checked += 1
            drop = after - base[r]
            worst = min(worst, drop)
            if drop < -SLACK:
                violations += 1
    return {"checked": checked, "violations": violations, "largest_drop": worst}


def reliability(y: list[int], chance: list[float]) -> tuple[tuple[float, float, int, float, float], ...]:
    """Predicted against observed deploy rate, in bands of estimate."""
    out = []
    for low, high in zip(EDGES[:-1], EDGES[1:]):
        picked = [i for i, c in enumerate(chance)
                  if low <= c < high or (high == EDGES[-1] and c == high)]
        if picked:
            said = sum(chance[i] for i in picked) / len(picked)
            seen = sum(y[i] for i in picked) / len(picked)
            out.append((low, high, len(picked), round(said, 4), round(seen, 4)))
    return tuple(out)


def run(matrix: str | Path = features.MATRIX,
        outcomes: str | Path = labels.LABELS,
        held_out: float = HELD_OUT, seed: int = SEED,
        loud: int = 0) -> Trained:
    """Load, split, choose a threshold, train, check, evaluate, and hand back 5.5's needs.

    loud is passed straight to the estimator's verbose setting, so a developer
    running this from a terminal can watch it fit. It defaults to silent,
    because this package's stdout carries MCP protocol frames.
    """
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import (accuracy_score, average_precision_score,
                                 brier_score_loss, confusion_matrix, f1_score,
                                 precision_score, recall_score, roc_auc_score)
    from sklearn.model_selection import (StratifiedKFold, cross_val_predict,
                                         train_test_split)

    x, y, names = load(matrix, outcomes)
    check(y)

    x_fit, x_test, y_fit, y_test = train_test_split(
        x, y, test_size=held_out, random_state=seed, stratify=y)
    logger.info("split %s rows into %s for training and %s held out, stratified",
                len(y), len(y_fit), len(y_test))

    # Each training row is predicted by a model that never saw it, and the
    # threshold is chosen on those predictions. The held out rows play no part.
    folds = StratifiedKFold(FOLDS, shuffle=True, random_state=seed)
    ahead = cross_val_predict(estimator(seed), x_fit, y_fit, cv=folds,
                              method="predict_proba")
    threshold = operating(y_fit, [float(row[1]) for row in ahead])
    logger.info("operating threshold %.3f, chosen by F1 on out of fold "
                "predictions over the training rows", threshold)

    model = estimator(seed, loud)
    model.fit(x_fit, y_fit)
    logger.info("fitted %s calibrated folds of %s trees",
                len(model.calibrated_classifiers_), PARAMS["max_iter"])

    held = monotone(model, x, names)
    logger.info("monotonic check over %s changes on %s rows: %s violation(s), "
                "largest drop %.2e", held["checked"], len(x), held["violations"],
                held["largest_drop"])
    if held["violations"]:
        raise TrainError(
            f"the fitted model lowered {held['violations']} estimate(s) when a failure "
            f"was cleared or a build succeeded, so the constraint did not survive "
            f"calibration and the model is refused")

    chance = chances(model, x_test)
    guessed = [1 if c >= threshold else 0 for c in chance]
    cells = confusion_matrix(y_test, guessed, labels=[0, 1])

    # What guessing from the stack alone would score, and every measure again
    # within each stack, where the stack tells the model nothing.
    node = names.index("is_node")
    overall = sum(y_fit) / len(y_fit)
    rate = {}
    for value in (0, 1):
        seen = [label for row, label in zip(x_fit, y_fit) if row[node] == value]
        rate[value] = sum(seen) / len(seen) if seen else overall
    baseline = float(average_precision_score(
        y_test, [rate.get(row[node], overall) for row in x_test]))
    within: dict[str, float] = {}
    by_stack: dict[str, dict] = {}
    for value, stack in ((0, "react_vite"), (1, "node_express")):
        picked = [i for i, row in enumerate(x_test) if row[node] == value]
        if not picked:
            continue
        truth = [y_test[i] for i in picked]
        seen = [chance[i] for i in picked]
        call = [guessed[i] for i in picked]
        part = confusion_matrix(truth, call, labels=[0, 1])
        entry = {
            "rows": len(picked), "positive": sum(truth),
            "brier": round(float(brier_score_loss(truth, seen)), 4),
            "precision": round(float(precision_score(truth, call, zero_division=0)), 4),
            "recall": round(float(recall_score(truth, call, zero_division=0)), 4),
            "f1": round(float(f1_score(truth, call, zero_division=0)), 4),
            "confusion": {"tn": int(part[0][0]), "fp": int(part[0][1]),
                          "fn": int(part[1][0]), "tp": int(part[1][1])},
            "reliability": [list(r) for r in reliability(truth, seen)],
        }
        if len(set(truth)) == 2:
            within[stack] = float(roc_auc_score(truth, seen))
            entry["roc_auc"] = round(within[stack], 4)
            entry["pr_auc"] = round(float(average_precision_score(truth, seen)), 4)
        by_stack[stack] = entry

    metrics = Metrics(
        accuracy=float(accuracy_score(y_test, guessed)),
        precision=float(precision_score(y_test, guessed, zero_division=0)),
        recall=float(recall_score(y_test, guessed, zero_division=0)),
        f1=float(f1_score(y_test, guessed, zero_division=0)),
        true_negative=int(cells[0][0]), false_positive=int(cells[0][1]),
        false_negative=int(cells[1][0]), true_positive=int(cells[1][1]),
        roc_auc=float(roc_auc_score(y_test, chance)),
        pr_auc=float(average_precision_score(y_test, chance)),
        brier=float(brier_score_loss(y_test, chance)),
        threshold=threshold,
        baseline=baseline,
        within=within,
        by_stack=by_stack,
        reliability=reliability(y_test, chance),
    )

    shuffled = permutation_importance(model, x_test, y_test, scoring="average_precision",
                                      n_repeats=REPEATS, random_state=seed)
    ranked = tuple(sorted(
        ((name, float(mean), float(spread)) for name, mean, spread
         in zip(names, shuffled.importances_mean, shuffled.importances_std)),
        key=lambda item: item[1], reverse=True))

    return Trained(model=model, names=names, trained=date.today().isoformat(),
                   metrics=metrics, rows=len(y), positive=sum(y),
                   negative=len(y) - sum(y), params=dict(PARAMS),
                   calibration=CALIBRATION, ranked=ranked, monotone=held)


def importances(trained: Trained) -> tuple[tuple[str, float], ...]:
    """Feature importances against module 5.2's real names, highest first.

    Permutation importances on the held out rows, computed in run: how much PR
    AUC falls when one feature is shuffled. A value at or below zero means
    shuffling that feature cost nothing measurable.
    """
    if not trained.ranked:
        raise TrainError("the model carries no importances, it was not trained by run")
    if len(trained.ranked) != len(trained.names):
        raise TrainError(
            f"the model has {len(trained.ranked)} importances but module 5.2 names "
            f"{len(trained.names)} features")
    return tuple((name, mean) for name, mean, _ in trained.ranked)


def save(trained: Trained, path: str | Path = ARTIFACT) -> Path:
    """Serialise the estimator with the metadata module 5.5 needs.

    The feature order is stored because a vector built in a different order
    would be silently wrong rather than an error, and the threshold because the
    gate judges every estimate against it.
    """
    import joblib

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": trained.model,
        "names": list(trained.names),
        "trained": trained.trained,
        "threshold": trained.threshold,
        "params": dict(trained.params),
        "calibration": trained.calibration,
        "constraints": constraints(trained.names),
        "monotone": dict(trained.monotone),
        "importances": [list(item) for item in trained.ranked],
        "metrics": trained.metrics.to_dict(),
        "rows": trained.rows,
        "positive": trained.positive,
        "negative": trained.negative,
    }, target)
    logger.info("wrote %s", target)
    return target


def report(trained: Trained) -> str:
    """The honest summary, for a terminal and for the phase report."""
    lines = [
        f"trained {trained.trained} on {trained.rows} labelled row(s)",
        f"  {trained.positive} positive, {trained.negative} negative, "
        f"{trained.balance:.1%} positive",
        f"  HistGradientBoostingClassifier, monotonic, {trained.params}, "
        f"{trained.calibration} calibration",
    ]
    if trained.monotone:
        lines.append(
            f"  monotonic check: {trained.monotone.get('checked')} changes, "
            f"{trained.monotone.get('violations')} lowered an estimate")
    if trained.imbalanced:
        lines.append(
            "  the dataset is imbalanced, so accuracy alone is misleading: "
            f"always answering the majority class would score "
            f"{max(trained.positive, trained.negative) / trained.rows:.1%}")
    lines.append("")
    lines.append(trained.metrics.report())
    lines.append("")
    lines.append("  permutation importances on the held out rows, fall in PR AUC, highest first")
    for rank, (name, mean, spread) in enumerate(trained.ranked, 1):
        lines.append(f"    {rank:>2}. {name:<24} {mean:+.4f}  spread {spread:.4f}")
    idle = [name for name, mean, _ in trained.ranked if mean <= 0]
    if idle:
        lines.append(f"    {len(idle)} feature(s) cost nothing measurable when shuffled: "
                     f"{', '.join(idle)}")
    return "\n".join(lines)
