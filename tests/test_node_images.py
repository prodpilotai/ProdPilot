"""Generated Dockerfiles and CI workflows build on the Node major the project declares.

The contracts are rendered exactly as the fix loop renders them, through the
audit and dispatch.instruct, on copies of committed samples.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from prodpilot import audit, buildtest, dispatch, extraction
from prodpilot.buildtest import BuildUnavailable
from prodpilot.extraction import ExtractError

SAMPLES = Path(__file__).resolve().parent / "samples"


def copy(tmp_path: Path, sample: str, engines: str | None = None,
         nvmrc: str | None = None, drop: tuple[str, ...] = ()) -> Path:
    root = tmp_path / sample
    shutil.copytree(SAMPLES / sample, root)
    for name in drop:
        (root / name).unlink()
    pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    if engines is None:
        pkg.pop("engines", None)
    else:
        pkg["engines"] = {"node": engines}
    (root / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    if nvmrc is not None:
        (root / ".nvmrc").write_text(nvmrc + "\n", encoding="utf-8")
    return root


def contract(root: Path, rule_id: str) -> str:
    report = audit.run(root)
    issue = dispatch.failing(report, rule_id)
    return dispatch.instruct(root, issue).to_dict()["contract"]["content"]


@pytest.mark.parametrize("engines,major", [
    (">=20 <21", 20), ("22.x", 22), ("^24", 24), ("26", 26), (None, 24),
])
def test_the_express_dockerfile_uses_the_declared_lts_line(tmp_path, engines, major):
    root = copy(tmp_path, "node_express_api", engines=engines)
    text = contract(root, "BLD-001")
    assert text.count(f"FROM node:{major}-alpine") == 2
    assert "node:20-alpine" not in text or major == 20


@pytest.mark.parametrize("engines,major", [(">=22 <23", 22), (None, 24), ("lts/jod", 22)])
def test_the_react_dockerfile_uses_the_declared_lts_line(tmp_path, engines, major):
    root = copy(tmp_path, "react_vite_app", engines=engines)
    text = contract(root, "BLD-007")
    assert text.startswith(f"FROM node:{major}-alpine AS build")
    assert "FROM nginx:1.27-alpine AS runtime" in text


def test_the_workflow_uses_the_same_major_as_the_image(tmp_path):
    root = copy(tmp_path, "node_express_api", engines="22")
    assert 'node-version: "22"' in contract(root, "BLD-003")
    assert "FROM node:22-alpine" in contract(root, "BLD-001")


def test_an_nvmrc_outranks_engines_as_it_does_on_render(tmp_path):
    root = copy(tmp_path, "node_express_api", engines=">=20 <21", nvmrc="24")
    assert "FROM node:24-alpine" in contract(root, "BLD-001")


def test_an_unsupported_major_uses_the_default(tmp_path):
    root = copy(tmp_path, "node_express_api", engines="18")
    assert "FROM node:24-alpine" in contract(root, "BLD-001")


@pytest.mark.parametrize("engines,candidates", [
    (">=18", ["Node 20", "Node 22", "Node 24", "Node 26"]),
    (">=20 <25", ["Node 20", "Node 22", "Node 24"]),
])
def test_an_ambiguous_range_refuses_and_lists_the_candidates(tmp_path, engines, candidates):
    root = copy(tmp_path, "node_express_api", engines=engines)
    report = audit.run(root)
    issue = dispatch.failing(report, "BLD-001")
    got = extraction.extract(extraction.load(root), issue)
    assert got.ok is False and "unclear" in got.reason
    assert list(got.candidates) == candidates
    with pytest.raises(ExtractError):
        dispatch.instruct(root, issue)
    with pytest.raises(ExtractError):
        dispatch.instruct(root, dispatch.failing(report, "BLD-003"))


# --------------------------------------------------------------------------
# real builds of the generated Dockerfile, where a Docker daemon is available
# --------------------------------------------------------------------------


def daemon() -> bool:
    try:
        buildtest.client()
        return True
    except BuildUnavailable:
        return False


needs_docker = pytest.mark.skipif(
    not daemon(), reason="no Docker daemon that runs Linux containers")


@needs_docker
@pytest.mark.parametrize("major", [22, 24])
def test_the_generated_dockerfile_builds_and_serves_on(tmp_path, major):
    root = copy(tmp_path, "docker_ok", engines=f"{major}.x", drop=("Dockerfile",))
    text = contract(root, "BLD-001")
    assert f"FROM node:{major}-alpine" in text
    (root / "Dockerfile").write_text(text, encoding="utf-8")

    result = buildtest.run(root, env={"PORT": "3000"}, wait=60)

    assert result.ok is True, result.detail
    assert result.health is not None and result.health.status == 200
