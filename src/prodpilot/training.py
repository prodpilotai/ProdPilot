"""Training and serialisation, Phase 5 module 5.4.

Scope is turning module 5.2's feature matrix and module 5.3's real labels into
one serialised model that module 5.5 can load and the scoring gate can read.

Section 6 names the estimator: a GradientBoostingClassifier, trained offline,
serialised with joblib, with feature importances reported. That is still the
estimator. It is wrapped in scikit-learn's sigmoid calibration, because the gate
reads its output as a probability, and a probability the gate acts on has to
mean what it says.

What this module refuses to do
-------------------------------
It refuses to produce a model it cannot justify. A dataset with one class in it
cannot train a classifier, and a dataset with a handful of rows cannot be
evaluated honestly, so both are errors rather than a model that would look
finished and mean nothing. Module 5.5 fails closed when the artifact is missing,
which only works if this module declines to write a worthless one.

How the configuration was chosen
---------------------------------
On the full labelled dataset, 684 rows of real Render outcomes, every choice was
made by cross validation on the training rows only, and the held out rows were
used once, to report the model that was chosen.

A grid search of 32 combinations around scikit-learn's defaults picked the
values in PARAMS. It barely moved the cross validated PR AUC, 0.507 for the
defaults against 0.514 tuned, well inside the spread between folds, so the honest
reading is that the defaults were already close to right. The tuned values are
kept because the procedure chose them, not because they are much better.

Why the imbalance is handled at the threshold, not with weights
-----------------------------------------------------------------
An earlier version weighted each row by the inverse frequency of its class.
Measured on the real dataset, that bought nothing in ranking, the PR AUC was
within noise either way, and it inflated every probability the gate reads: its
Brier score was 0.158 against 0.121 once calibrated without weights. So the
model is trained on the data as it is and calibrated to the real deploy rate,
and the imbalance is handled where it belongs, in the decision threshold.

That threshold is the operating point that maximises F1 on out of fold
predictions over the training rows. It is stored in the artifact, because a
calibrated model on a dataset that is 19 percent positive rarely goes above 0.5,
and scoring it at 0.5 reports an F1 near zero for a model that ranks well.

Why the split is stratified
----------------------------
The positive class in this dataset is rare, because most public repositories do
not deploy and serve their health route unchanged. A plain random split can put
every positive row on one side, which makes the held out metrics meaningless. A
stratified split keeps the same class balance in both halves.

Twenty five percent is held out. That is enough to see the confusion matrix move
without starving a small training set, and the seed is fixed so a rerun produces
the same split and the same numbers.

What the report measures against
---------------------------------
Accuracy is reported but never led with. Beside the model's ranking scores the
report gives the PR AUC of guessing from the stack alone, because the two stacks
deploy at very different rates and is_node is one of the features, so a model
could look good by learning nothing but the stack. It also gives the ROC AUC
within each stack, where the stack itself tells the model nothing.

Why the metadata travels with the model
----------------------------------------
A serialised estimator alone is not enough to use safely. Module 5.5 has to
build a feature vector in exactly the order this model was trained on, judge the
estimate against the threshold chosen here, and a reader has to know when it was
trained, how, and how well it did. All of that is written into the artifact
beside the estimator rather than kept in someone's notes.
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

# Held out for evaluation. Small enough to leave a usable training set, large
# enough that the confusion matrix means something.
HELD_OUT = 0.25

# Fixed so a rerun reproduces the split, the model and the reported numbers.
SEED = 20260910

# Below this there is nothing to evaluate honestly. A confusion matrix over a
# handful of rows is noise with a table around it.
FLOOR = 20

# Training cross validates twice, once to calibrate and once to choose the
# threshold. With fewer rows of the rarer class than this, some folds would hold
# none of it, and calibrating on a fold with one class is meaningless.
MINORITY = 10

# Chosen by grid search on the training rows of the real dataset. The module
# docstring says how, and how little it mattered.
PARAMS = {"n_estimators": 100, "learning_rate": 0.1, "max_depth": 3,
          "min_samples_leaf": 1, "subsample": 0.8}

# Sigmoid rather than isotonic. On the real training rows it gave the lower
# Brier score and the higher PR AUC of the two, and isotonic regression needs
# more data than this to be trusted.
CALIBRATION = "sigmoid"
FOLDS = 5


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


def estimator(seed: int = SEED, loud: int = 0):
    """The model: a GradientBoostingClassifier inside sigmoid calibration."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier

    return CalibratedClassifierCV(
        GradientBoostingClassifier(random_state=seed, verbose=loud, **PARAMS),
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


def run(matrix: str | Path = features.MATRIX,
        outcomes: str | Path = labels.LABELS,
        held_out: float = HELD_OUT, seed: int = SEED,
        loud: int = 0) -> Trained:
    """Load, split, choose a threshold, train, evaluate, and hand back 5.5's needs.

    loud is passed straight to the estimator's verbose setting, so a developer
    running this from a terminal can watch it fit. It defaults to silent,
    because this package's stdout carries MCP protocol frames.
    """
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
                len(model.calibrated_classifiers_), PARAMS["n_estimators"])

    chance = chances(model, x_test)
    guessed = [1 if c >= threshold else 0 for c in chance]
    cells = confusion_matrix(y_test, guessed, labels=[0, 1])

    # What guessing from the stack alone would score, and how well the model
    # ranks once the stack is fixed.
    node = names.index("is_node")
    overall = sum(y_fit) / len(y_fit)
    rate = {}
    for value in (0, 1):
        seen = [label for row, label in zip(x_fit, y_fit) if row[node] == value]
        rate[value] = sum(seen) / len(seen) if seen else overall
    baseline = float(average_precision_score(
        y_test, [rate.get(row[node], overall) for row in x_test]))
    within: dict[str, float] = {}
    for value, stack in ((0, "react_vite"), (1, "node_express")):
        picked = [i for i, row in enumerate(x_test) if row[node] == value]
        truth = [y_test[i] for i in picked]
        if len(set(truth)) == 2:
            within[stack] = float(roc_auc_score(truth, [chance[i] for i in picked]))

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
    )

    return Trained(model=model, names=names, trained=date.today().isoformat(),
                   metrics=metrics, rows=len(y), positive=sum(y),
                   negative=len(y) - sum(y), params=dict(PARAMS),
                   calibration=CALIBRATION)


def importances(trained: Trained) -> tuple[tuple[str, float], ...]:
    """Feature importances against module 5.2's real names, not indices.

    Section 6 asks for these to be reported and sensible, which cannot be
    judged from a column number. A calibrated model holds one fitted ensemble
    per calibration fold, and their importances are averaged.
    """
    model = trained.model
    folds = getattr(model, "calibrated_classifiers_", None)
    if folds:
        arrays = [getattr(fold.estimator, "feature_importances_", None) for fold in folds]
        if any(found is None for found in arrays):
            raise TrainError("a calibration fold reported no feature importances")
        found = [sum(float(a[i]) for a in arrays) / len(arrays)
                 for i in range(len(arrays[0]))]
    else:
        found = getattr(model, "feature_importances_", None)
        if found is None:
            raise TrainError("the estimator reported no feature importances")
    if len(found) != len(trained.names):
        raise TrainError(
            f"the model has {len(found)} features but module 5.2 names "
            f"{len(trained.names)}")
    pairs = tuple(zip(trained.names, (float(v) for v in found)))
    return tuple(sorted(pairs, key=lambda p: p[1], reverse=True))


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
        f"  GradientBoostingClassifier {trained.params}, "
        f"{trained.calibration} calibration",
    ]
    if trained.imbalanced:
        lines.append(
            "  the dataset is imbalanced, so accuracy alone is misleading: "
            f"always answering the majority class would score "
            f"{max(trained.positive, trained.negative) / trained.rows:.1%}")
    lines.append("")
    lines.append(trained.metrics.report())
    lines.append("")
    lines.append("  feature importances, highest first")
    ranked = importances(trained)
    for name, value in ranked:
        if value <= 0:
            continue
        lines.append(f"    {name:<24} {value:.4f}")
    unused = [n for n, v in ranked if v <= 0]
    if unused:
        lines.append(f"    {len(unused)} feature(s) contributed nothing: "
                     f"{', '.join(unused)}")
    return "\n".join(lines)
