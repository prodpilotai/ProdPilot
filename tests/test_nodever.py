"""Declared Node versions, the end-of-life notice, and what never changes because of them."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from prodpilot import audit, nodever
from prodpilot.nodever import DEFAULT, choose, declarations, notices, resolve

SAMPLES = Path(__file__).resolve().parent / "samples"
AFTER_20 = date(2026, 10, 10)
BEFORE_20 = date(2026, 4, 1)


def project(tmp_path: Path, engines: str | None = None, nvmrc: str | None = None,
            node_version: str | None = None, env: str | None = None) -> Path:
    root = tmp_path / "app"
    root.mkdir(parents=True)
    pkg: dict = {"name": "app", "dependencies": {"express": "^4.19.0"}}
    if engines is not None:
        pkg["engines"] = {"node": engines}
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    if nvmrc is not None:
        (root / ".nvmrc").write_text(nvmrc + "\n", encoding="utf-8")
    if node_version is not None:
        (root / ".node-version").write_text(node_version + "\n", encoding="utf-8")
    if env is not None:
        (root / ".env").write_text(env, encoding="utf-8")
    return root


# ---------------------------------------------------------------- detection

def test_engines_alone_is_read(tmp_path: Path) -> None:
    found = declarations(project(tmp_path, engines=">=22 <23"))
    assert [(d.source, d.spec) for d in found] == [("package.json engines.node", ">=22 <23")]


def test_nvmrc_alone_is_read(tmp_path: Path) -> None:
    found = declarations(project(tmp_path, nvmrc="v24.4.1"))
    assert [(d.source, d.spec) for d in found] == [(".nvmrc", "v24.4.1")]


def test_node_version_file_alone_is_read(tmp_path: Path) -> None:
    found = declarations(project(tmp_path, node_version="22"))
    assert [(d.source, d.spec) for d in found] == [(".node-version", "22")]


def test_env_node_version_alone_is_read(tmp_path: Path) -> None:
    found = declarations(project(tmp_path, env="PORT=3000\nNODE_VERSION=22.11.0\n"))
    assert [(d.source, d.spec) for d in found] == [(".env NODE_VERSION", "22.11.0")]


def test_conflicting_sources_follow_render_precedence(tmp_path: Path) -> None:
    """Render reads NODE_VERSION, then .node-version, then .nvmrc, then engines."""
    root = project(tmp_path, engines=">=20 <21", nvmrc="22", node_version="24",
                   env="NODE_VERSION=26\n")
    assert [d.source for d in declarations(root)] == [
        ".env NODE_VERSION", ".node-version", ".nvmrc", "package.json engines.node"]
    assert choose(root).resolved.major == 26
    (root / ".env").unlink()
    assert choose(root).resolved.major == 24
    (root / ".node-version").unlink()
    assert choose(root).resolved.major == 22
    (root / ".nvmrc").unlink()
    assert choose(root).resolved.major == 20


def test_nothing_declared_defaults_to_the_active_lts_line(tmp_path: Path) -> None:
    chosen = choose(project(tmp_path))
    assert chosen.declared == () and chosen.resolved.major == DEFAULT == 24


@pytest.mark.parametrize("spec,major", [
    ("20", 20), ("22.x", 22), ("v24.4.1", 24), ("^22", 22), ("~24.1", 24),
    (">=20 <21", 20), (">=22.0.0 <23.0.0", 22), ("lts/jod", 22), ("26", 26),
])
def test_one_supported_major_is_used(spec: str, major: int) -> None:
    assert resolve(spec).major == major


@pytest.mark.parametrize("spec", ["18", "16.20.2", "25", "27"])
def test_an_unsupported_major_falls_back_to_the_default(spec: str) -> None:
    assert resolve(spec).major == DEFAULT


@pytest.mark.parametrize("spec,candidates", [
    (">=18", (20, 22, 24, 26)), (">20", (22, 24, 26)), (">=20 <25", (20, 22, 24)),
    ("^20 || ^22", (20, 22)), ("lts/*", (20, 22, 24, 26)), ("node", (20, 22, 24, 26)),
    ("not a version", (20, 22, 24, 26)),
])
def test_an_ambiguous_range_names_its_candidates(spec: str, candidates: tuple) -> None:
    found = resolve(spec)
    assert found.ambiguous and found.candidates == candidates and found.reason


async def test_the_detection_tool_carries_the_node_version_in_the_blueprint() -> None:
    from test_detect_stack_tool import connected_session, payload_of

    async with connected_session() as client:
        result = await client.call_tool("prodpilot_detect_stack",
                                        {"project_path": str(SAMPLES / "node_express_ready")})
    payload = payload_of(result)
    node = payload["blueprint"]["node"]
    assert node["source"] == "package.json engines.node"
    assert node["spec"] == ">=20 <21" and node["image_major"] == 20
    assert isinstance(payload["blueprint"]["notices"], list)


# ---------------------------------------------------------------- end-of-life notice

def test_an_end_of_life_major_gets_a_notice(tmp_path: Path) -> None:
    said = notices(project(tmp_path, nvmrc="18"), AFTER_20)
    assert len(said) == 1
    assert "Node 18" in said[0] and "2025-04-30" in said[0]
    assert "does not affect the score or the gate" in said[0]


def test_node_20_is_end_of_life_only_from_its_date(tmp_path: Path) -> None:
    root = project(tmp_path, engines=">=20 <21")
    assert notices(root, BEFORE_20) == ()
    (said,) = notices(root, AFTER_20)
    assert "2026-04-30" in said and "BLD-005" in said


def test_a_supported_current_major_gets_no_notice(tmp_path: Path) -> None:
    assert notices(project(tmp_path, engines="24"), AFTER_20) == ()
    assert notices(project(tmp_path / "x", ), AFTER_20) == ()


def test_conflicting_declarations_are_reported(tmp_path: Path) -> None:
    said = notices(project(tmp_path, engines="22", nvmrc="24"), AFTER_20)
    assert len(said) == 1 and "Render uses .nvmrc" in said[0]


def test_the_notice_changes_no_verdict_score_or_gate_input(tmp_path: Path) -> None:
    """Same project, once with and once without an end-of-life declaration file."""
    plain = tmp_path / "plain"
    shutil.copytree(SAMPLES / "node_express_ready", plain)
    noted = tmp_path / "noted"
    shutil.copytree(SAMPLES / "node_express_ready", noted)
    (noted / ".node-version").write_text("18\n", encoding="utf-8")

    a, b = audit.run(plain), audit.run(noted)
    assert b.notices and not [n for n in a.notices if "18" in n]
    assert a.score == b.score and a.band == b.band
    assert [(r.rule_id, r.status) for r in a.results] == [(r.rule_id, r.status) for r in b.results]
    assert a.blockers == b.blockers
    assert "notices" not in b.to_dict()  # the 1.0.0 report dictionary keeps its shape


def test_end_of_life_dates_match_the_release_schedule() -> None:
    assert nodever.END_OF_LIFE[20] == date(2026, 4, 30)
    assert nodever.END_OF_LIFE[22] == date(2027, 4, 30)
    assert nodever.END_OF_LIFE[24] == date(2028, 4, 30)
    assert nodever.SUPPORTED == (20, 22, 24, 26)
