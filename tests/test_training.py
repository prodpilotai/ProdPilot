"""Training and serialisation tests for Phase 5 module 5.4.

The data here is synthetic and small on purpose. These tests check that the
module joins, splits, calibrates, chooses its threshold, evaluates and
serialises correctly, and that it refuses the datasets it cannot handle
honestly. They are not a claim about how well the real model performs, which
only the real labelled dataset can say.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from prodpilot import features, training
from prodpilot.training import (
    ARTIFACT,
    CALIBRATION,
    FLOOR,
    FOLDS,
    HELD_OUT,
    MINORITY,
    PARAMS,
    Metrics,
    TrainError,
    check,
    constraints,
    estimator,
    importances,
    load,
    monotone,
    operating,
    report,
    run,
    save,
)

SIZE = features.SIZE


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Three shuffles per feature rather than thirty.

    The importance code is the same; only the number of repeats differs, and
    thirty of them on every training run here made this file take half an hour.
    """
    monkeypatch.setattr(training, "REPEATS", 3)


def vector(seed: int, positive: bool) -> list[int]:
    """A feature row with a real signal in it, so a model can learn something.

    failed_p0 and failed_security are pushed in opposite directions by the
    label, which is the kind of relationship the real model is meant to find.
    """
    values = [(seed + i) % 3 for i in range(SIZE)]
    values[features.FEATURES.index("failed_p0")] = 0 if positive else 4
    values[features.FEATURES.index("failed_security")] = 0 if positive else 3
    values[features.FEATURES.index("is_node")] = seed % 2
    values[features.FEATURES.index("built")] = 1 if positive else seed % 2
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


def test_every_row_carries_every_feature(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=5, negatives=5)

    x, _, names = load(matrix, outcomes)

    assert len(names) == SIZE
    assert all(len(row) == SIZE for row in x)


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
    """A model built on it would mean nothing."""
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
    with pytest.raises(TrainError) as caught:
        check([0] * 40 + [1])

    assert "rarer class" in str(caught.value)


def test_too_few_of_the_rarer_class_to_calibrate_is_refused():
    """Training cross validates twice, and some folds would hold none of it."""
    with pytest.raises(TrainError) as caught:
        check([0] * 60 + [1] * (MINORITY - 1))

    assert str(MINORITY) in str(caught.value)


def test_a_workable_dataset_passes_the_check():
    assert check([0] * 30 + [1] * MINORITY) is None


def test_training_on_one_class_raises_rather_than_writing_a_model(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=0, negatives=40)

    with pytest.raises(TrainError):
        run(matrix, outcomes)

    assert not (tmp_path / "model.joblib").exists()


# --------------------------------------------------------------------------
# the split, the calibration and the threshold
# --------------------------------------------------------------------------


def test_the_held_out_share_is_a_quarter():
    assert HELD_OUT == 0.25


def test_the_seed_is_fixed_so_a_rerun_reproduces_the_numbers(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    first = run(matrix, outcomes)
    second = run(matrix, outcomes)

    assert first.metrics.to_dict() == second.metrics.to_dict()
    assert importances(first) == importances(second)


def test_the_split_keeps_the_rare_class_in_both_halves(tmp_path: Path):
    """A plain random split can put every positive on one side, which makes
    the held out metrics meaningless."""
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=40)

    trained = run(matrix, outcomes)

    assert trained.metrics.true_positive + trained.metrics.false_negative >= 1
    assert trained.metrics.true_negative + trained.metrics.false_positive >= 1


def test_the_model_is_calibrated(tmp_path: Path):
    """The gate reads its output as a probability, so it has to be one."""
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    trained = run(matrix, outcomes)

    assert trained.calibration == CALIBRATION == "sigmoid"
    assert len(trained.model.calibrated_classifiers_) == FOLDS


def test_the_model_uses_the_parameters_the_search_chose(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    trained = run(matrix, outcomes)
    fitted = trained.model.calibrated_classifiers_[0].estimator

    assert trained.params == PARAMS
    for key, value in PARAMS.items():
        assert getattr(fitted, key) == value
    assert list(fitted.monotonic_cst) == constraints()
    assert fitted.early_stopping is False


def test_the_model_is_trained_without_class_weights():
    """Weights inflated every probability the gate reads and bought nothing in
    ranking, so the imbalance is handled at the threshold instead."""
    assert "sample_weight" not in inspect.getsource(training.run)
    assert not hasattr(training, "weigh")


def test_the_threshold_is_the_best_f1_point():
    """Perfectly separable predictions have one right cut."""
    assert operating([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 0.8


def test_the_threshold_prefers_catching_the_rarer_class():
    cut = operating([0, 0, 0, 0, 1, 1], [0.05, 0.1, 0.3, 0.35, 0.3, 0.6])

    assert cut <= 0.3, "a cut above 0.3 would miss a positive for no gain"


def test_the_threshold_is_chosen_on_training_rows_only():
    """The held out rows report the model; they never tune it."""
    source = inspect.getsource(training.run)
    chosen = source.index("threshold = operating(")

    assert "cross_val_predict" in source[:chosen]
    assert "x_test" not in source[source.index("ahead = "):chosen]


def test_the_threshold_is_stored_as_a_probability(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    trained = run(matrix, outcomes)

    assert 0.0 < trained.threshold < 1.0
    assert trained.threshold == trained.metrics.threshold


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
    """A sanity check on the pipeline, not a claim about the real dataset.

    Large enough that each calibration fold trains on more than a hundred rows,
    so the estimator's minimum leaf size lets it split at all.
    """
    matrix, outcomes = dataset(tmp_path, positives=60, negatives=120)

    trained = run(matrix, outcomes)

    assert trained.metrics.roc_auc > 0.8, trained.metrics.report()


def test_ranking_and_calibration_are_measured(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    metrics = run(matrix, outcomes).metrics

    assert 0.0 <= metrics.roc_auc <= 1.0
    assert 0.0 <= metrics.pr_auc <= 1.0
    assert 0.0 <= metrics.brier <= 1.0


def test_the_report_measures_against_guessing_from_the_stack(tmp_path: Path):
    """is_node is a feature and the stacks deploy at different rates, so a
    model could look good by learning nothing but the stack."""
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    trained = run(matrix, outcomes)

    assert 0.0 <= trained.metrics.baseline <= 1.0
    assert "guessing from the stack alone" in report(trained)


def test_within_stack_ranking_is_reported_when_measurable(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    within = run(matrix, outcomes).metrics.within

    assert set(within) <= {"react_vite", "node_express"}
    assert all(0.0 <= v <= 1.0 for v in within.values())


def test_importances_are_named_not_numbered(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    found = importances(run(matrix, outcomes))

    assert len(found) == SIZE
    assert all(name in features.FEATURES for name, _ in found)
    assert found == tuple(sorted(found, key=lambda p: p[1], reverse=True))


def test_importances_are_the_permutation_importances_run_measured(tmp_path: Path):
    """HistGradientBoostingClassifier reports no impurity importances, so these
    are how much PR AUC fell on the held out rows when each feature was shuffled."""
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    trained = run(matrix, outcomes)

    assert len(trained.ranked) == SIZE
    assert dict(importances(trained)) == {name: mean for name, mean, _ in trained.ranked}
    assert all(spread >= 0 for _, _, spread in trained.ranked)
    assert not hasattr(trained.model.calibrated_classifiers_[0].estimator,
                       "feature_importances_")


def test_the_feature_the_data_depends_on_ranks_highly(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=60, negatives=120)

    top = [name for name, _ in importances(run(matrix, outcomes))[:3]]

    assert "failed_p0" in top or "failed_security" in top


def test_imbalance_is_stated_rather_than_hidden(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=12, negatives=80)

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


def test_the_report_shows_the_confusion_matrix_at_the_threshold(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    said = report(run(matrix, outcomes))

    assert "confusion matrix" in said
    assert "operating threshold" in said
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

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    found = joblib.load(save(run(matrix, outcomes), tmp_path / "model.joblib"))

    assert found["names"] == list(features.FEATURES)
    assert len(found["names"]) == SIZE


def test_the_artifact_carries_the_threshold_and_how_it_was_made(tmp_path: Path):
    """Module 5.5 judges every estimate against this threshold."""
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)
    trained = run(matrix, outcomes)

    found = joblib.load(save(trained, tmp_path / "model.joblib"))

    assert found["threshold"] == trained.threshold
    assert found["params"] == PARAMS
    assert found["calibration"] == CALIBRATION


def test_the_artifact_carries_the_training_date_and_metrics(tmp_path: Path):
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)
    trained = run(matrix, outcomes)

    found = joblib.load(save(trained, tmp_path / "model.joblib"))

    assert found["trained"] == trained.trained
    assert found["metrics"]["roc_auc"] == round(trained.metrics.roc_auc, 4)
    assert "confusion" in found["metrics"]
    assert found["rows"] == 44


def test_the_saved_model_still_predicts(tmp_path: Path):
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)
    trained = run(matrix, outcomes)

    back = joblib.load(save(trained, tmp_path / "model.joblib"))["model"]

    assert list(back.predict_proba([vector(1, True)])[0]) == list(
        trained.model.predict_proba([vector(1, True)])[0])


def test_the_saved_model_gives_a_probability(tmp_path: Path):
    """Module 5.5 reads a calibrated probability, not a class."""
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)
    back = joblib.load(save(run(matrix, outcomes), tmp_path / "model.joblib"))

    chance = back["model"].predict_proba([vector(1, True)])[0]

    assert len(chance) == 2
    assert abs(sum(chance) - 1.0) < 1e-9


def test_the_artifact_directory_is_created_if_absent(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    path = save(run(matrix, outcomes), tmp_path / "deep" / "here" / "model.joblib")

    assert path.is_file()


def test_the_default_artifact_path_is_under_data():
    assert ARTIFACT.parent.name == "data"
    assert ARTIFACT.name.endswith(".joblib")


def test_the_metrics_serialise_whole(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    payload = run(matrix, outcomes).to_dict()

    json.dumps(payload)
    assert set(payload) == {"names", "trained", "rows", "positive", "negative",
                            "balance", "imbalanced", "threshold", "params",
                            "calibration", "constraints", "monotone", "importances",
                            "metrics"}


def test_training_writes_nothing_to_stdout(tmp_path: Path, capsys):
    """This package's stdout carries MCP protocol frames."""
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    run(matrix, outcomes)

    assert capsys.readouterr().out == ""


def test_the_estimator_can_be_asked_to_speak_for_a_terminal(tmp_path: Path, capsys):
    """The visible training run a developer watches passes loud through."""
    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    run(matrix, outcomes, loud=2)

    assert "tree" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------
# the monotonic constraint
# --------------------------------------------------------------------------


def test_every_failure_is_constrained_down_and_the_build_up():
    """scikit-learn holds the constraint over the positive class, a deploy,
    so -1 means more failures can only lower the estimate."""
    found = dict(zip(features.FEATURES, constraints()))

    assert all(found[n] == -1 for n in features.FEATURES if n.startswith("failed_"))
    assert found["built"] == 1
    assert all(found[n] == 0 for n in features.FEATURES
               if not n.startswith("failed_") and n != "built")


def test_the_estimator_is_the_one_that_can_carry_the_constraint():
    from sklearn.ensemble import GradientBoostingClassifier, HistGradientBoostingClassifier

    base = estimator().estimator

    assert isinstance(base, HistGradientBoostingClassifier)
    assert list(base.monotonic_cst) == constraints()
    assert "monotonic_cst" not in GradientBoostingClassifier().get_params()


def test_clearing_a_failure_or_fixing_the_build_never_lowers_the_estimate(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)
    trained = run(matrix, outcomes)
    x, _, names = load(matrix, outcomes)

    checked = monotone(trained.model, x, names)

    assert checked["checked"] > 0
    assert checked["violations"] == 0
    assert trained.monotone["violations"] == 0


class Backwards:
    """A model that rewards failures, which the check has to catch."""

    classes_ = [0, 1]

    def predict_proba(self, rows):
        at = features.FEATURES.index("failed_p0")
        out = []
        for row in rows:
            p = min(0.9, 0.1 + 0.2 * row[at])
            out.append([1 - p, p])
        return out


def test_a_model_that_moves_the_wrong_way_is_caught():
    row = [0] * SIZE
    row[features.FEATURES.index("failed_p0")] = 3

    found = monotone(Backwards(), [row], list(features.FEATURES))

    assert found["violations"] >= 1
    assert found["largest_drop"] < 0


def test_a_model_that_breaks_the_constraint_is_refused(tmp_path: Path, monkeypatch):
    from prodpilot import training

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)
    monkeypatch.setattr(training, "monotone",
                        lambda model, x, names: {"checked": 1, "violations": 1,
                                                 "largest_drop": -0.2})

    with pytest.raises(TrainError) as caught:
        run(matrix, outcomes)

    assert "refused" in str(caught.value)


# --------------------------------------------------------------------------
# every measure, for each stack on its own
# --------------------------------------------------------------------------


def test_every_held_out_measure_is_reported_for_each_stack(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    metrics = run(matrix, outcomes).metrics

    assert set(metrics.by_stack) == {"react_vite", "node_express"}
    for found in metrics.by_stack.values():
        assert {"rows", "positive", "brier", "precision", "recall", "f1",
                "confusion", "reliability"} <= set(found)
    assert sum(f["rows"] for f in metrics.by_stack.values()) == metrics.tested


def test_the_calibration_table_covers_every_held_out_row(tmp_path: Path):
    matrix, outcomes = dataset(tmp_path, positives=20, negatives=40)

    metrics = run(matrix, outcomes).metrics

    assert sum(rows for _, _, rows, _, _ in metrics.reliability) == metrics.tested
    assert all(0.0 <= said <= 1.0 and 0.0 <= seen <= 1.0
               for _, _, _, said, seen in metrics.reliability)


def test_the_artifact_carries_its_constraint(tmp_path: Path):
    import joblib

    matrix, outcomes = dataset(tmp_path, positives=14, negatives=30)

    found = joblib.load(save(run(matrix, outcomes), tmp_path / "model.joblib"))

    assert found["constraints"] == constraints()
    assert found["monotone"]["violations"] == 0
