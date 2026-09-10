"""Training and serialisation tests for Phase 5 module 5.4.

The data here is synthetic and small on purpose. These tests check that the
module joins, splits, weighs, evaluates and serialises correctly, and that it
refuses the datasets it cannot handle honestly. They are not a claim about how
well the real model performs, which only the real labelled dataset can say.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import features, training
from prodpilot.training import (
    ARTIFACT,
    FLOOR,
    HELD_OUT,
    SEED,
    Metrics,
    TrainError,
    check,
    importances,
    load,
    report,
    run,
    save,
    weigh,
)

SIZE = features.SIZE


def vector(seed: int, positive: bool) -> list[int]:
    """A feature row with a real signal in it, so a model can learn something.

    failed_p0 and failed_security are pushed in opposite directions by the
    label, which is the kind of relationship the real model is meant to find.
    """
    values = [(seed + i) % 3 for i in range(SIZE)]
    values[features.FEATURES.index("failed_p0")] = 0 if positive else 4
    values[features.FEATURES.index("failed_security")] = 0 if positive else 3
    values[features.FEATURES.index("is_node")] = seed % 2
    return values


def dataset(tmp_path: Path, positives: int, negatives: int) -> tuple[Path, Path]:
    """A feature file and a label file that join on module 5.2's identity."""
    rows, made = [], []
    for index in range(positives + negatives):
        good = index < positives
        name = f"octo/repo{index}"
        rows.append({"name": name, "kind": "repo", "rule_id": "", "stack": "node_express",
                     "commit": "a" * 40, "score": 50, "values": vector(index, good)})
        made.append({"name": name, "kind": "repo", "rule_id": "", "commit": "a" * 40,
                     "label": 1 if good else 0, "stage": "post-deploy smoke test",
                     "detail": "ok" if good else "failed"})

    matrix = tmp_path / "features.jsonl"
    outcomes = tmp_path / "labels.jsonl"
    matrix.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    outcomes.write_text("\n".join(json.dumps(r) for r in made), encoding="utf-8")
    return matrix, outcomes


# --------------------------------------------------------------------------
# loading and joining
# --------------------------------------------------------------------------


def test_features_and_labels_are_joined_into_a_matrix(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=10, negatives=20)

    x, y, names = load(matrix, outcomes)

    assert len(x) == 30
    assert len(y) == 30
    assert sum(y) == 10
    assert names == features.FEATURES


def test_every_row_carries_all_twenty_five_features(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=5, negatives=5)

    x, _, names = load(matrix, outcomes)

    assert len(names) == 25
    assert all(len(row) == 25 for row in x)


def test_a_feature_row_with_no_label_is_not_trained_on(tmp_path: Path):
    """Module 5.3's join leaves it out, and this must not invent one."""
    matrix, outcomes = dataset(tmp_path, positives=6, negatives=6)
    extra = {"name": "octo/unlabelled", "kind": "repo", "rule_id": "",
             "commit": "b" * 40, "score": 10, "values": vector(99, False)}
    with matrix.open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps(extra))

    x, y, _ = load(matrix, outcomes)

    assert len(y) == 12


def test_a_missing_label_file_says_to_run_module_5_3(tmp_path: Path):
    matrix, _ = dataset(tmp_path, positives=2, negatives=2)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(TrainError) as caught:
        load(matrix, empty)

    assert "module 5.3" in str(caught.value)


def test_a_join_that_matches_nothing_is_refused(tmp_path: Path):
    matrix, _ = dataset(tmp_path, positives=2, negatives=2)
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps({"name": "someone/else", "kind": "repo",
                                 "rule_id": "", "commit": "c" * 40, "label": 1,
                                 "stage": "smoke", "detail": ""}), encoding="utf-8")

    with pytest.raises(TrainError) as caught:
        load(matrix, other)

    assert "join produced nothing" in str(caught.value)


# --------------------------------------------------------------------------
# what this module refuses to train
# --------------------------------------------------------------------------


def test_a_dataset_with_one_class_is_refused():
    """The likely real outcome, and a model built on it would mean nothing."""
    with pytest.raises(TrainError) as caught:
        check([0] * 50)

    assert "nothing to learn" in str(caught.value)
    assert "module 5.3" in str(caught.value)


def test_a_dataset_of_all_positives_is_refused_too():
    with pytest.raises(TrainError):
        check([1] * 50)


def test_too_few_rows_to_evaluate_is_refused():
    y = [0] * (FLOOR - 5) + [1] * 3

    with pytest.raises(TrainError) as caught:
        check(y)

    assert "too few" in str(caught.value)


def test_a_single_row_of_the_rarer_class_cannot_be_split():
    y = [0] * 40 + [1]

    with pytest.raises(TrainError) as caught:
        check(y)

    assert "rarer class" in str(caught.value)


def test_a_workable_dataset_passes_the_check():
    assert check([0] * 30 + [1] * 10) is None


def test_training_on_one_class_raises_rather_than_writing_a_model(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=0, negatives=40)

    with pytest.raises(TrainError):
        run(matrix, outcomes)

    assert not (tmp_path / "model.joblib").exists()


# --------------------------------------------------------------------------
# the split and the weights
# --------------------------------------------------------------------------


def test_the_held_out_share_is_a_quarter():
    assert HELD_OUT == 0.25


def test_the_seed_is_fixed_so_a_rerun_reproduces_the_numbers(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    first = run(matrix, outcomes)
    second = run(matrix, outcomes)

    assert first.metrics.to_dict() == second.metrics.to_dict()
    assert importances(first) == importances(second)


def test_the_split_keeps_the_rare_class_in_both_halves(tmp_path: Path):
    """A plain random split can put every positive on one side, which makes
    the held out metrics meaningless."""
    matrix, outcomes = dataset(tmp_path, positives=8, negatives=32)

    trained = run(matrix, outcomes)

    # Stratified, so the test set must contain at least one of each class.
    assert trained.metrics.true_positive + trained.metrics.false_negative >= 1
    assert trained.metrics.true_negative + trained.metrics.false_positive >= 1


def test_the_rarer_class_is_weighted_up():
    """GradientBoostingClassifier has no class_weight, so this is where
    balancing has to happen."""
    y = [0] * 90 + [1] * 10

    sample, per_class = weigh(y)

    assert per_class["1"] > per_class["0"]
    assert len(sample) == 100
    # per_class is the rounded figure written into the artifact for a reader.
    # The weights that reach fit are exact.
    assert sample[0] == pytest.approx(per_class["0"], abs=1e-3)
    assert sample[-1] == pytest.approx(per_class["1"], abs=1e-3)
    assert sample[0] == pytest.approx(100 / (2 * 90))
    assert sample[-1] == pytest.approx(100 / (2 * 10))


def test_a_balanced_dataset_weighs_both_classes_the_same():
    _, per_class = weigh([0] * 50 + [1] * 50)

    assert per_class["0"] == per_class["1"] == 1.0


# --------------------------------------------------------------------------
# what the model reports
# --------------------------------------------------------------------------


def test_a_model_is_trained_and_evaluated_on_held_out_rows(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=15, negatives=35)

    trained = run(matrix, outcomes)

    assert trained.rows == 50
    assert trained.positive == 15
    assert trained.negative == 35
    assert trained.metrics.tested == 13, "a quarter of 50, rounded"
    assert 0.0 <= trained.metrics.accuracy <= 1.0


def test_the_learnable_signal_is_actually_learned(tmp_path: Path):
    """A sanity check on the pipeline, not a claim about the real dataset."""
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    trained = run(matrix, outcomes)

    assert trained.metrics.accuracy > 0.7, trained.metrics.report()


def test_importances_are_named_not_numbered(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    found = importances(run(matrix, outcomes))

    assert len(found) == 25
    assert all(name in features.FEATURES for name, _ in found)
    assert found == tuple(sorted(found, key=lambda p: p[1], reverse=True))


def test_the_feature_the_data_depends_on_ranks_highly(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    top = [name for name, _ in importances(run(matrix, outcomes))[:3]]

    assert "failed_p0" in top or "failed_security" in top


def test_imbalance_is_stated_rather_than_hidden(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=4, negatives=40)

    trained = run(matrix, outcomes)

    assert trained.imbalanced is True
    said = report(trained)
    assert "imbalanced" in said
    assert "accuracy alone is misleading" in said


def test_a_balanced_dataset_is_not_called_imbalanced(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=24)

    trained = run(matrix, outcomes)

    assert trained.imbalanced is False
    assert "imbalanced" not in report(trained)


def test_the_report_shows_the_confusion_matrix(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    said = report(run(matrix, outcomes))

    assert "confusion matrix" in said
    assert "predicted 0" in said
    assert "actual 1" in said


def test_the_confusion_cells_add_up_to_the_held_out_rows():
    metrics = Metrics(0.8, 0.5, 0.5, 0.5, 6, 1, 1, 2)

    assert metrics.tested == 10


# --------------------------------------------------------------------------
# serialisation, and the metadata module 5.5 needs
# --------------------------------------------------------------------------


def test_the_artifact_carries_the_feature_order(tmp_path: Path):
    """A vector built in a different order would be silently wrong."""
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)
    trained = run(matrix, outcomes)

    path = save(trained, tmp_path / "model.joblib")
    found = joblib.load(path)

    assert found["names"] == list(features.FEATURES)
    assert len(found["names"]) == 25


def test_the_artifact_carries_the_training_date_and_metrics(tmp_path: Path):
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)
    trained = run(matrix, outcomes)

    found = joblib.load(save(trained, tmp_path / "model.joblib"))

    assert found["trained"] == trained.trained
    assert found["metrics"]["accuracy"] == round(trained.metrics.accuracy, 4)
    assert "confusion" in found["metrics"]
    assert found["rows"] == 40


def test_the_saved_model_still_predicts(tmp_path: Path):
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)
    trained = run(matrix, outcomes)

    back = joblib.load(save(trained, tmp_path / "model.joblib"))["model"]

    assert list(back.predict([vector(1, True)])) == list(
        trained.model.predict([vector(1, True)]))


def test_the_saved_model_gives_a_probability(tmp_path: Path):
    """Module 5.5 reads a calibrated probability, not a class."""
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)
    back = joblib.load(save(run(matrix, outcomes), tmp_path / "model.joblib"))

    chance = back["model"].predict_proba([vector(1, True)])[0]

    assert len(chance) == 2
    assert abs(sum(chance) - 1.0) < 1e-9


def test_the_artifact_directory_is_created_if_absent(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    path = save(run(matrix, outcomes), tmp_path / "deep" / "here" / "model.joblib")

    assert path.is_file()


def test_the_default_artifact_path_is_under_data():
    assert ARTIFACT.parent.name == "data"
    assert ARTIFACT.name.endswith(".joblib")


def test_the_metrics_serialise_whole(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    payload = run(matrix, outcomes).to_dict()

    json.dumps(payload)
    assert set(payload) == {"names", "trained", "rows", "positive", "negative",
                            "balance", "imbalanced", "metrics"}


def test_training_writes_nothing_to_stdout(tmp_path: Path, capsys):
    """This package's stdout carries MCP protocol frames."""
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    run(matrix, outcomes)

    assert capsys.readouterr().out == ""


def test_the_estimator_can_be_asked_to_speak_for_a_terminal(tmp_path: Path, capsys):
    """The visible training run a developer watches passes loud through."""
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=28)

    run(matrix, outcomes, loud=2)

    assert "Iter" in capsys.readouterr().out
