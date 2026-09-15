"""The whole chain on real samples, from the audit to the ninth ProdPush stage.

Module 7.3's integration test. tests/pipeline.py runs the chain on one sample
and records a trace; this runs it on a representative set, a clean project of
each stack, two broken ones and one outside the supported stacks, and holds each
trace to what must be true of any run, then to the outcome recorded for that
sample in docs/pipeline.md.

Stage 3 builds real images, and the gate builds the project to ask the model, so
this needs a Docker daemon and is skipped without one, as the stage's own tests
are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import KEY, TOKEN, trace
from prodpilot import buildtest, config
from prodpilot.buildtest import BuildUnavailable

REPO = Path(__file__).resolve().parents[1]


def daemon() -> bool:
    try:
        buildtest.client()
        return True
    except BuildUnavailable:
        return False


pytestmark = pytest.mark.skipif(not daemon(), reason="no Docker daemon available")

SAMPLES = [
    "node_express_gated",
    "react_vite_ready+entry",
    "node_express_insecure",
    "node_express_secrets",
    "unrecognized_python_service",
]

# What each did in the run recorded in docs/pipeline.md: completed all nine
# stages, or was refused at the gate.
WIRED = {"node_express_gated", "react_vite_ready+entry", "node_express_insecure"}


@pytest.fixture
def work(tmp_path: Path, monkeypatch) -> Path:
    """Credentials outside the project, and the repository root the model lives in."""
    monkeypatch.setenv(config.CONFIG_HOME_ENV_VAR, str(tmp_path / "home"))
    config.save_credentials(config.Credentials(github_token=TOKEN, render_api_key=KEY))
    monkeypatch.chdir(REPO)
    return tmp_path


@pytest.mark.parametrize("sample", SAMPLES)
def test_the_chain_holds_together(sample: str, work: Path) -> None:
    record = trace(sample, work)
    steps = record["prodpush"]["steps"]

    assert record["unhandled"] is None, record["unhandled"]
    assert [s["stage"] for s in steps][0] == "scoring gate" and len(steps) == 9

    # Once a stage fails, nothing after it runs.
    failed = next((i for i, s in enumerate(steps) if s["ran"] and not s["ok"]), None)
    if failed is not None:
        assert not any(s["ran"] for s in steps[failed + 1:])

    # The gate's decision and ProdPush's first stage agree.
    assert steps[0]["ok"] is record["gate"]["ready"]
    if not record["gate"]["ready"]:
        assert not any(s["ran"] for s in steps[1:])

    # A run that went the whole way pushed, and every stage passed or did not apply.
    if record["prodpush"]["ok"]:
        assert record["prodpush"]["mirror_commits"] >= 1
        assert all(s["ok"] for s in steps)

    # And the outcome recorded for this sample.
    if sample in WIRED:
        assert record["prodpush"]["ok"] is True, record["prodpush"]["failed_stage"]
    else:
        assert record["gate"]["ready"] is False
        assert record["prodpush"]["failed_stage"] == "scoring gate"
