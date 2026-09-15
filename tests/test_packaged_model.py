"""The gate's model ships inside the package.

An IDE starts the server in the developer's own project, which holds no model.
Before this, the gate looked for data/model.joblib relative to the working
directory, so for anyone who installed ProdPilot it found nothing and refused
every project.
"""

from __future__ import annotations

import re
from pathlib import Path

import sklearn

from prodpilot import features, scoring, training

REPO = Path(__file__).resolve().parents[1]


def test_the_model_is_found_from_any_working_directory(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    model = scoring.load()

    assert tuple(model.names) == tuple(features.FEATURES)
    assert 0 < model.threshold < 1


def test_the_model_lives_inside_the_package():
    package = Path(scoring.__file__).resolve().parent

    assert scoring.ARTIFACT.is_file()
    assert package in scoring.ARTIFACT.resolve().parents


def test_training_still_writes_where_the_dataset_lives():
    """Promoting a retrained model is a deliberate copy, never a side effect."""
    assert training.ARTIFACT == Path("data") / "model.joblib"
    assert training.ARTIFACT != scoring.ARTIFACT


def test_scikit_learn_is_pinned_to_the_version_installed():
    """A saved estimator is only guaranteed to load under the version that saved it."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    pinned = re.search(r'"scikit-learn==([0-9.]+)"', text)

    assert pinned is not None
    assert pinned.group(1) == sklearn.__version__
