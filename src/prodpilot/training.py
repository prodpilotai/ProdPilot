"""Training and serialisation, Phase 5 module 5.4.

Scope is turning module 5.2's feature matrix and module 5.3's real labels into
one serialised model that module 5.5 can load and the scoring gate can read.

Section 6 names the estimator: a GradientBoostingClassifier, trained offline,
serialised with joblib, with feature importances reported. Nothing here invents
a different one.

What this module refuses to do
-------------------------------
It refuses to produce a model it cannot justify. A dataset with one class in it
cannot train a classifier, and a dataset with a handful of rows cannot be
evaluated honestly, so both are errors rather than a model that would look
finished and mean nothing. Module 5.5 fails closed when the artifact is missing,
which only works if this module declines to write a worthless one.

Why the split is stratified
----------------------------
The positive class in this dataset is rare, because most public repositories do
not deploy and pass a smoke test unchanged. A plain random split can put every
positive row on one side, which makes the held out metrics meaningless. A
stratified split keeps the same class balance in both halves, so the test set
still contains the class the model is supposed to find.

Twenty five percent is held out. That is enough to see the confusion matrix
move without starving a small training set, and the seed is fixed so a rerun
produces the same split and the same numbers.

Why imbalance is handled with sample weights
---------------------------------------------
GradientBoostingClassifier has no class_weight parameter. Its reference puts
weighting on fit, as sample_weight, so that is where the balancing goes. Each
row is weighted by the inverse frequency of its class, which stops the model
from reaching high accuracy by predicting the majority class every time.

Accuracy is reported but never trusted on its own here. On a dataset that is
ninety five percent negative, always answering negative scores ninety five, so
precision, recall and the confusion matrix are what the report leads with.

Why the metadata travels with the model
----------------------------------------
A serialised estimator alone is not enough to use safely. Module 5.5 has to
build a feature vector in exactly the order this model was trained on, and a
reader has to know when it was trained and how well it did. All of that is
written into the artifact beside the estimator rather than kept in someone's
notes.
"""

from __future__ import annotations

import json
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


class TrainError(Exception):
    """Raised when a model cannot be trained or serialised honestly."""


@dataclass(frozen=True)
class Metrics:
    """How the model did on rows it never saw.

    The four confusion matrix cells are kept, not just the ratios, because on a
    small imbalanced test set the raw counts are what a reader needs to judge
    whether a ratio means anything.
    """

    accuracy: float
    precision: float
    recall: float
    f1: float
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int

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
    weights: dict[str, float] = field(default_factory=dict)

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


def weigh(y: list[int]) -> tuple[list[float], dict[str, float]]:
    """One weight per row, by inverse class frequency.

    GradientBoostingClassifier takes no class_weight, so balancing happens here
    and travels into fit as sample_weight.
    """
    counts = {0: y.count(0), 1: y.count(1)}
    total = len(y)
    per = {label: (total / (2 * n) if n else 0.0) for label, n in counts.items()}
    return [per[label] for label in y], {str(k): round(v, 4) for k, v in per.items()}


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
    if fewest < 2:
        raise TrainError(
            f"only {fewest} row(s) carry the rarer class, which cannot be split "
            f"into a training and a held out set")


def run(matrix: str | Path = features.MATRIX,
        outcomes: str | Path = labels.LABELS,
        held_out: float = HELD_OUT, seed: int = SEED,
        loud: int = 0) -> Trained:
    """Load, split, train, evaluate, and hand back everything 5.5 needs.

    loud is passed straight to the estimator's verbose setting, so a developer
    running this from a terminal can watch it fit. It defaults to silent,
    because this package's stdout carries MCP protocol frames.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                                 precision_score, recall_score)
    from sklearn.model_selection import train_test_split

    x, y, names = load(matrix, outcomes)
    check(y)

    x_fit, x_test, y_fit, y_test = train_test_split(
        x, y, test_size=held_out, random_state=seed, stratify=y)
    logger.info("split %s rows into %s for training and %s held out, stratified",
                len(y), len(y_fit), len(y_test))

    sample, per_class = weigh(y_fit)
    model = GradientBoostingClassifier(random_state=seed, verbose=loud)
    model.fit(x_fit, y_fit, sample_weight=sample)
    logger.info("fitted %s trees", getattr(model, "n_estimators_", "?"))

    guessed = model.predict(x_test)
    cells = confusion_matrix(y_test, guessed, labels=[0, 1])
    metrics = Metrics(
        accuracy=float(accuracy_score(y_test, guessed)),
        precision=float(precision_score(y_test, guessed, zero_division=0)),
        recall=float(recall_score(y_test, guessed, zero_division=0)),
        f1=float(f1_score(y_test, guessed, zero_division=0)),
        true_negative=int(cells[0][0]), false_positive=int(cells[0][1]),
        false_negative=int(cells[1][0]), true_positive=int(cells[1][1]),
    )

    return Trained(model=model, names=names, trained=date.today().isoformat(),
                   metrics=metrics, rows=len(y), positive=sum(y),
                   negative=len(y) - sum(y), weights=per_class)


def importances(trained: Trained) -> tuple[tuple[str, float], ...]:
    """Feature importances against module 5.2's real names, not indices.

    Section 6 asks for these to be reported and sensible, which cannot be
    judged from a column number.
    """
    found = getattr(trained.model, "feature_importances_", None)
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
    would be silently wrong rather than an error.
    """
    import joblib

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": trained.model,
        "names": list(trained.names),
        "trained": trained.trained,
        "metrics": trained.metrics.to_dict(),
        "rows": trained.rows,
        "positive": trained.positive,
        "negative": trained.negative,
        "weights": trained.weights,
    }, target)
    logger.info("wrote %s", target)
    return target


def report(trained: Trained) -> str:
    """The honest summary, for a terminal and for the phase report."""
    lines = [
        f"trained {trained.trained} on {trained.rows} labelled row(s)",
        f"  {trained.positive} positive, {trained.negative} negative, "
        f"{trained.balance:.1%} positive",
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
    for name, value in importances(trained):
        if value <= 0:
            continue
        lines.append(f"    {name:<24} {value:.4f}")
    unused = [n for n, v in importances(trained) if v <= 0]
    if unused:
        lines.append(f"    {len(unused)} feature(s) contributed nothing: "
                     f"{', '.join(unused)}")
    return "\n".join(lines)
