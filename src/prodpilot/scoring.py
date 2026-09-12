"""Runtime integration, Phase 5 module 5.5.

Scope is making module 5.4's serialised model usable by the scoring gate. This
module loads the artifact, turns an audit report into the calibrated 0 to 100
score Section 6 describes, and nothing else. It decides nothing about
deployment; module 4.3 wires it into gate.reading and the gate keeps its own
threshold and its own blocker rule.

Loaded once, not per call
--------------------------
Reading a joblib artifact costs real time and the gate can be asked repeatedly
in one run, once per loop cycle. The artifact is read the first time it is
needed and then held, so a five cycle run pays for it once. reset exists for
tests, which need to be able to forget it.

Why it fails closed and says so
--------------------------------
A missing or corrupted artifact must never quietly become the provisional audit
score. The two numbers mean different things: one is the audit engine's own
weighted opinion, the other is a model's estimate of whether the project will
actually deploy. Silently swapping one for the other would report a calibrated
score that was never calibrated, and nobody reading the result could tell.

So a failure here produces no score at all. The gate treats an absent score as
not ready, which is the fail closed behaviour module 4.2 already built, and the
reason is logged at error level with what actually went wrong.

Why the feature order is checked rather than trusted
------------------------------------------------------
The artifact carries the column order it was trained on. A vector built in a
different order is still the same count of numbers, so nothing would raise: the
model would simply answer confidently about the wrong thing. Comparing the
stored names against module 5.2's own FEATURES on load turns that silent
wrongness into a refusal. That check is also what refuses a 25 feature model
trained before the build feature existed.

The build result is the caller's to supply
-------------------------------------------
Every feature but one comes from the audit report. Whether the project builds
does not, so estimate and score take it as an argument: the gate runs the build
check and passes what it found. There is no default, because a guessed build
result is a guessed estimate.

What the gate does with it
---------------------------
The probability is not the gate's score. On the real dataset no project, even
one with every rule fixed, reaches a calibrated 0.9, so a gate at 90 on this
number would never open. Module 4.3 therefore keeps the audit score, its
threshold and its bands, and asks this module a second question beside them:
is the estimate at or above the operating point module 5.4 chose and stored in
the artifact. That point is read from the artifact rather than written here, so
the model and the decision it is judged by cannot drift apart. No band boundary
appears in this file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from prodpilot import features
from prodpilot.audit import Report
from prodpilot.training import ARTIFACT

logger = logging.getLogger(__name__)


class ScoreError(Exception):
    """Raised when a calibrated score cannot be produced."""


@dataclass(frozen=True)
class Model:
    """A loaded model and the metadata that makes it safe to use."""

    model: object
    names: tuple[str, ...]
    trained: str
    metrics: dict
    rows: int = 0
    threshold: float = 0.5

    def chance(self, values) -> float:
        """The model's probability that this project deploys and serves health."""
        if len(values) != len(self.names):
            raise ScoreError(
                f"the project produced {len(values)} features but the model was "
                f"trained on {len(self.names)}")
        found = self.model.predict_proba([list(values)])[0]
        classes = list(getattr(self.model, "classes_", [0, 1]))
        if 1 not in classes:
            raise ScoreError("the model has no positive class to report on")
        return float(found[classes.index(1)])

    def score(self, values) -> int:
        """The calibrated probability as a 0 to 100 number, for display."""
        return max(0, min(100, round(self.chance(values) * 100)))

    def clears(self, values) -> bool:
        """Whether the estimate reaches the operating point 5.4 chose."""
        return self.chance(values) >= self.threshold

    def to_dict(self) -> dict[str, object]:
        return {"trained": self.trained, "rows": self.rows,
                "features": len(self.names), "threshold": self.threshold,
                "metrics": self.metrics}


# Held between calls. The gate asks once per cycle and the artifact does not
# change while a run is in progress.
_held: Model | None = None


def load(path: str | Path | None = None) -> Model:
    """Read the artifact and check it is usable, or raise saying why."""
    # Resolved at call time, not bound as a default, so a test can point the
    # module at a different artifact.
    target = Path(path if path is not None else ARTIFACT)
    if not target.is_file():
        raise ScoreError(
            f"no trained model at {target}. Run module 5.4 to train one. The "
            f"gate will not fall back to the audit engine score.")

    try:
        import joblib
        found = joblib.load(target)
    except Exception as exc:
        raise ScoreError(f"the model at {target} could not be read: {exc}") from exc

    if not isinstance(found, dict):
        raise ScoreError(f"the model at {target} is not a ProdPilot artifact")

    missing = [key for key in ("model", "names", "trained")
               if not found.get(key)]
    if missing:
        raise ScoreError(
            f"the model at {target} is missing {', '.join(missing)}")

    names = tuple(str(n) for n in found["names"])
    if names != tuple(features.FEATURES):
        raise ScoreError(
            "the model was trained on a different feature set than module 5.2 "
            "produces, so its answers would describe the wrong columns")

    model = found["model"]
    if not hasattr(model, "predict_proba"):
        raise ScoreError("the stored estimator cannot report a probability")

    # The gate judges every estimate against this, so a model without one
    # cannot be used, however good its estimates are.
    if "threshold" not in found:
        raise ScoreError(
            f"the model at {target} carries no operating threshold, so its "
            f"estimates could not be judged. Retrain it with module 5.4.")
    try:
        threshold = float(found["threshold"])
    except (TypeError, ValueError) as exc:
        raise ScoreError(f"the operating threshold in {target} is not a number") from exc
    if not 0.0 < threshold < 1.0:
        raise ScoreError(f"the operating threshold {threshold} is not a probability")

    logger.info("loaded the model trained on %s, %s row(s), operating threshold %.3f",
                found["trained"], found.get("rows", "?"), threshold)
    return Model(model=model, names=names, trained=str(found["trained"]),
                 metrics=dict(found.get("metrics") or {}),
                 rows=int(found.get("rows") or 0), threshold=threshold)


def held(path: str | Path | None = None) -> Model:
    """The loaded model, read once and kept."""
    global _held
    if _held is None:
        _held = load(path)
    return _held


def reset() -> None:
    """Forget the held model. For tests, and for a retrain in one process."""
    global _held
    _held = None


def score(report: Report, built: int, path: str | Path | None = None) -> int:
    """The calibrated probability for one audit report, as 0 to 100.

    The feature vector is module 5.2's own, so the runtime and the training set
    are built by the same code and cannot drift apart.
    """
    return held(path).score(features.vector(report, built))


def estimate(report: Report, built: int,
             path: str | Path | None = None) -> tuple[float, float]:
    """The probability this project deploys, and the operating point it faces.

    What module 4.3 asks for. Both come from the one held artifact, so the
    estimate is always judged against the threshold chosen for that model.
    built is 1 when the project's own build succeeded and 0 when it failed.
    """
    made = held(path)
    return made.chance(features.vector(report, built)), made.threshold
