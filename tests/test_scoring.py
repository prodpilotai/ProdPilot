"""Runtime integration tests for Phase 5 module 5.5.

The artifact under test is a real joblib file holding a real fitted estimator,
written by module 5.4's own save. Mocking the model would test nothing that
matters here, since what this module does is load one safely and refuse when it
cannot.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from prodpilot import audit, features, scoring, training
from prodpilot.audit import RuleResult, band_of, score_of
from prodpilot.findings import Status
from prodpilot.scoring import Model, ScoreError, held, load, reset, score

SAMPLES = Path(__file__).resolve().parent / "samples"


@pytest.fixture(autouse=True)
def forget():
    """No test may inherit another's held model."""
    reset()
    yield
    reset()


@pytest.fixture(scope="module")
def report():
    return audit.run(SAMPLES / "react_vite_ready")


@pytest.fixture(scope="module")
def artifact(tmp_path_factory, report) -> Path:
    """A real model, trained and serialised through module 5.4's own code."""
    import random

    ids = [r.rule_id for r in report.results]
    rng = random.Random(11)
    rows, labels = [], []
    for _ in range(300):
        count = rng.randint(0, 8)
        failing = set(rng.sample(ids, count))
        results = [RuleResult(rule=r.rule,
                              status=Status.FAIL if r.rule_id in failing else Status.PASS,
                              evidence=r.evidence)
                   for r in report.results]
        made = dataclasses.replace(report, results=results, score=score_of(results))
        rows.append({"name": f"octo/r{len(rows)}", "kind": "repo", "rule_id": "",
                     "commit": "a" * 40, "score": made.score,
                     "values": list(features.vector(made, 1))})
        labels.append({"name": f"octo/r{len(labels)}", "kind": "repo", "rule_id": "",
                       "commit": "a" * 40,
                       "label": 1 if not made.blockers and count <= 2 else 0,
                       "stage": "post-deploy smoke test", "detail": ""})

    home = tmp_path_factory.mktemp("artifact")
    matrix, outcomes = home / "features.jsonl", home / "labels.jsonl"
    matrix.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    outcomes.write_text("\n".join(json.dumps(r) for r in labels), encoding="utf-8")

    return training.save(training.run(matrix, outcomes), home / "model.joblib")


@pytest.fixture
def wired(artifact, monkeypatch):
    monkeypatch.setattr(scoring, "ARTIFACT", artifact)
    return artifact


# --------------------------------------------------------------------------
# loading, and refusing to
# --------------------------------------------------------------------------


def test_a_real_artifact_loads_with_its_metadata(wired):
    made = load()

    assert made.names == features.FEATURES
    assert made.trained
    assert made.rows == 300
    assert "accuracy" in made.metrics


def test_a_missing_artifact_says_to_train_one(tmp_path: Path):
    with pytest.raises(ScoreError) as caught:
        load(tmp_path / "nothing.joblib")

    assert "module 5.4" in str(caught.value)
    assert "will not fall back" in str(caught.value)


def test_a_corrupted_artifact_is_refused(tmp_path: Path):
    broken = tmp_path / "model.joblib"
    broken.write_bytes(b"this is not a joblib file at all")

    with pytest.raises(ScoreError) as caught:
        load(broken)

    assert "could not be read" in str(caught.value)


def test_a_file_that_is_not_a_prodpilot_artifact_is_refused(tmp_path: Path):
    import joblib

    other = tmp_path / "model.joblib"
    joblib.dump(["not", "a", "dict"], other)

    with pytest.raises(ScoreError) as caught:
        load(other)

    assert "not a ProdPilot artifact" in str(caught.value)


def test_an_artifact_missing_its_metadata_is_refused(tmp_path: Path, wired):
    import joblib

    found = joblib.load(wired)
    del found["names"]
    stripped = tmp_path / "model.joblib"
    joblib.dump(found, stripped)

    with pytest.raises(ScoreError) as caught:
        load(stripped)

    assert "missing names" in str(caught.value)


def test_a_model_trained_on_different_columns_is_refused(tmp_path: Path, wired):
    """A vector in the wrong order is still 25 numbers, so nothing would raise.

    The model would simply answer confidently about the wrong thing.
    """
    import joblib

    found = joblib.load(wired)
    found["names"] = list(reversed(found["names"]))
    reordered = tmp_path / "model.joblib"
    joblib.dump(found, reordered)

    with pytest.raises(ScoreError) as caught:
        load(reordered)

    assert "different feature set" in str(caught.value)


def test_an_estimator_that_cannot_give_a_probability_is_refused(tmp_path: Path, wired):
    import joblib

    found = joblib.load(wired)
    found["model"] = object()
    useless = tmp_path / "model.joblib"
    joblib.dump(found, useless)

    with pytest.raises(ScoreError) as caught:
        load(useless)

    assert "cannot report a probability" in str(caught.value)


# --------------------------------------------------------------------------
# loaded once, not per call
# --------------------------------------------------------------------------


def test_the_model_is_read_once_and_kept(wired, monkeypatch):
    """The gate asks once per loop cycle, up to five times in a run."""
    reads = []
    real = scoring.load

    def counted(path=None):
        reads.append(path)
        return real(path)

    monkeypatch.setattr(scoring, "load", counted)

    for _ in range(5):
        held()

    assert len(reads) == 1


def test_reset_makes_it_read_again(wired, monkeypatch):
    reads = []
    real = scoring.load
    monkeypatch.setattr(scoring, "load",
                        lambda path=None: (reads.append(path), real(path))[1])

    held()
    reset()
    held()

    assert len(reads) == 2


def test_a_failure_to_load_is_not_cached_as_a_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(scoring, "ARTIFACT", tmp_path / "absent.joblib")

    with pytest.raises(ScoreError):
        held()

    assert scoring._held is None


# --------------------------------------------------------------------------
# the score itself
# --------------------------------------------------------------------------


def test_a_report_becomes_a_score_in_the_documented_range(wired, report):
    found = score(report, 1)

    assert isinstance(found, int)
    assert 0 <= found <= 100


def test_the_score_is_the_probability_of_the_positive_class(wired):
    class Half:
        classes_ = [0, 1]

        def predict_proba(self, rows):
            return [[0.63, 0.37] for _ in rows]

    made = Model(Half(), features.FEATURES, "2026-09-10", {})

    assert made.score([0] * features.SIZE) == 37


def test_the_positive_class_is_found_by_label_not_by_position(wired):
    """classes_ order is the model's, not an assumption this module may make."""
    class Reversed:
        classes_ = [1, 0]

        def predict_proba(self, rows):
            return [[0.8, 0.2] for _ in rows]

    made = Model(Reversed(), features.FEATURES, "2026-09-10", {})

    assert made.score([0] * features.SIZE) == 80


def test_a_model_with_no_positive_class_is_refused():
    class OnlyNegative:
        classes_ = [0]

        def predict_proba(self, rows):
            return [[1.0] for _ in rows]

    made = Model(OnlyNegative(), features.FEATURES, "2026-09-10", {})

    with pytest.raises(ScoreError) as caught:
        made.score([0] * features.SIZE)

    assert "no positive class" in str(caught.value)


def test_a_vector_of_the_wrong_width_is_refused():
    class Any:
        classes_ = [0, 1]

        def predict_proba(self, rows):
            return [[0.5, 0.5] for _ in rows]

    made = Model(Any(), features.FEATURES, "2026-09-10", {})

    with pytest.raises(ScoreError) as caught:
        made.score([0] * (features.SIZE - 1))

    assert f"{features.SIZE - 1} features" in str(caught.value)


def test_a_worse_project_scores_lower_than_a_better_one(wired, report):
    """The relationship the model is supposed to carry, on real reports."""
    def with_failing(failing):
        results = [RuleResult(rule=r.rule,
                              status=Status.FAIL if r.rule_id in failing else Status.PASS,
                              evidence=r.evidence)
                   for r in report.results]
        return dataclasses.replace(report, results=results, score=score_of(results))

    clean = score(with_failing(set()), 1)
    broken = score(with_failing({r.rule_id for r in report.results[:8]}), 1)

    assert clean > broken


def test_the_bands_are_module_2_4s_and_not_restated(wired, report):
    """Section 6 fixes the bands, and module 4.3 changes only the source."""
    source = Path(scoring.__file__).read_text(encoding="utf-8")

    assert "Band" not in source.replace("bands", "")
    assert band_of(score(report, 1)) is not None


def test_a_report_the_audit_could_not_judge_has_no_features(wired, report):
    """features.vector refuses, and the gate turns that into no score."""
    empty = dataclasses.replace(report, results=[], score=0)

    with pytest.raises(features.FeatureError):
        score(empty, 1)


def test_the_feature_vector_is_module_5_2s_own(wired):
    """The runtime and the training set cannot drift apart if they share it."""
    source = Path(scoring.__file__).read_text(encoding="utf-8")

    assert "features.vector(report, built)" in source


def test_the_model_summarises_itself_for_a_report(wired):
    payload = load().to_dict()

    json.dumps(payload)
    assert payload["features"] == features.SIZE
    assert payload["rows"] == 300



# --------------------------------------------------------------------------
# the operating threshold the gate judges every estimate against
# --------------------------------------------------------------------------


def test_the_threshold_travels_from_training_to_the_loaded_model(wired):
    import joblib

    stored = joblib.load(wired)["threshold"]
    made = load()

    assert made.threshold == stored
    assert 0.0 < made.threshold < 1.0


def test_an_artifact_with_no_threshold_is_refused(tmp_path: Path, wired):
    """The gate judges every estimate against it, so without one the model
    cannot be used, however good its estimates are."""
    import joblib

    found = joblib.load(wired)
    del found["threshold"]
    stripped = tmp_path / "model.joblib"
    joblib.dump(found, stripped)

    with pytest.raises(ScoreError) as caught:
        load(stripped)

    assert "operating threshold" in str(caught.value)


def test_a_threshold_that_is_not_a_probability_is_refused(tmp_path: Path, wired):
    import joblib

    found = joblib.load(wired)
    found["threshold"] = 1.5
    broken = tmp_path / "model.joblib"
    joblib.dump(found, broken)

    with pytest.raises(ScoreError):
        load(broken)


def test_the_estimate_comes_with_its_operating_point(wired, report):
    chance, operating = scoring.estimate(report, 1)

    assert 0.0 <= chance <= 1.0
    assert operating == load().threshold


def test_clearing_is_judged_at_the_stored_threshold():
    class Fixed:
        classes_ = [0, 1]

        def predict_proba(self, rows):
            return [[0.7, 0.3] for _ in rows]

    low = Model(Fixed(), features.FEATURES, "2026-09-12", {}, threshold=0.25)
    high = Model(Fixed(), features.FEATURES, "2026-09-12", {}, threshold=0.35)

    assert low.clears([0] * features.SIZE) is True
    assert high.clears([0] * features.SIZE) is False
