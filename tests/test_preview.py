"""prodpilot fix --dry-run: diffs, refusals, constraint contracts, loop order, no writes."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prodpilot import preview
from prodpilot.cli import app
from prodpilot.reporting import mask

from test_sarif import KEY, plant

SAMPLES = Path(__file__).resolve().parent / "samples"
ALL = sorted(p.name for p in SAMPLES.iterdir() if p.is_dir())


def by_rule(planned) -> dict[str, preview.Planned]:
    return {p.rule_id: p for p in planned}


def test_a_new_file_is_shown_against_dev_null() -> None:
    found = by_rule(preview.plan(SAMPLES / "node_express_secrets"))["BLD-002"]
    assert found.kind == preview.DIFF
    (path, diff), = found.diffs
    assert path == ".dockerignore"
    assert diff.startswith("--- /dev/null\n+++ b/.dockerignore\n")
    assert "+node_modules" in diff


def test_an_edit_is_a_unified_diff_of_the_file() -> None:
    found = by_rule(preview.plan(SAMPLES / "node_express_secrets"))["BLD-005"]
    (path, diff), = found.diffs
    assert path == "package.json"
    assert diff.startswith("--- a/package.json\n+++ b/package.json\n@@")
    assert '+    "node": ">=20 <21"' in diff


def test_a_parametric_refusal_names_its_candidates() -> None:
    found = by_rule(preview.plan(SAMPLES / "amb_two_lockfiles"))
    for rule_id in ("BLD-001", "BLD-003"):
        refused = found[rule_id]
        assert refused.kind == preview.REFUSED
        assert "package manager is unclear" in refused.reason
        assert refused.candidates == ("package-lock.json", "yarn.lock")
        assert "candidates: package-lock.json, yarn.lock" in refused.text()


def test_a_delegated_rule_shows_its_constraint_contract() -> None:
    planned = preview.plan(SAMPLES / "node_express_insecure")
    delegated = [p for p in planned if p.fix_type == "DYNAMIC-DELEGATED"]
    assert delegated
    for p in delegated:
        assert p.kind == preview.CONSTRAINT and not p.diffs
        for key in ("requirement", "boundary", "forbidden", "checker"):
            assert p.contract[key], key
        assert p.contract["checker"].startswith("prodpilot.")
        assert "no diff" in p.text()


def test_the_order_is_the_loops_with_dependencies_named() -> None:
    planned = preview.plan(SAMPLES / "node_express_secrets")
    ids = [p.rule_id for p in planned]
    # P0 first in the loop's own order, except a change that waits for the
    # rule creating its file, which comes after that rule.
    scr = by_rule(planned)["SCR-001"]
    assert scr.depends_on == (("GIT-001", ".gitignore"),)
    assert ids.index("GIT-001") < ids.index("SCR-001")
    assert scr.cycle == 2
    assert "depends on GIT-001, which creates .gitignore" in scr.text()
    first_cycle = [p.priority for p in planned if p.cycle == 1]
    assert first_cycle == sorted(first_cycle)


def test_one_rule_can_be_previewed_alone() -> None:
    planned = preview.plan(SAMPLES / "node_express_secrets", rule="SCR-001")
    assert [p.rule_id for p in planned] == ["SCR-001"]
    assert planned[0].depends_on == (("GIT-001", ".gitignore"),)


def test_a_rule_that_is_not_failing_is_refused() -> None:
    with pytest.raises(preview.PreviewError, match="not failing"):
        preview.plan(SAMPLES / "node_express_gated", rule="SEC-002")


def test_a_credential_never_appears_in_the_preview(tmp_path: Path) -> None:
    root = plant(tmp_path)
    text = "\n".join(p.text() for p in preview.plan(root))
    data = json.dumps([p.to_dict() for p in preview.plan(root)])
    for out in (text, data):
        assert KEY not in out and KEY[4:] not in out
        assert KEY[:6] + "..." + KEY[-4:] not in out
    assert mask(KEY) in text


# --------------------------------------------------------------------------
# the no-write guarantee
# --------------------------------------------------------------------------


def digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def leftovers() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("prodpilot-preview-*")}


@pytest.mark.parametrize("sample", ALL)
def test_a_dry_run_writes_nothing(sample: str, tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / sample
    shutil.copytree(SAMPLES / sample, root)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.name=ProdPilot Test", "-c",
                    "user.email=test@prodpilot.invalid", "-C", str(root), "commit", "-q",
                    "-m", "sample"], check=True)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    before, temp_before = digest(root), leftovers()

    done = CliRunner().invoke(app, ["fix", "--dry-run", str(root)])

    assert done.exit_code == 0, done.output
    assert "Nothing was written." in done.output
    assert digest(root) == before
    assert list(workdir.iterdir()) == []
    assert leftovers() == temp_before


# --------------------------------------------------------------------------
# the command and the tool
# --------------------------------------------------------------------------


def test_fix_without_dry_run_is_a_usage_error() -> None:
    done = CliRunner().invoke(app, ["fix", str(SAMPLES / "react_vite_app")])
    assert done.exit_code == 2


def test_fix_dry_run_for_one_rule(tmp_path: Path) -> None:
    done = CliRunner().invoke(app, ["fix", "--dry-run", "--rule", "BLD-002",
                                    str(SAMPLES / "node_express_secrets")])
    assert done.exit_code == 0
    assert done.output.startswith("BLD-002 (P1, STATIC)\n--- /dev/null")


async def test_the_fix_tool_returns_the_preview_on_request(tmp_path: Path) -> None:
    from test_detect_stack_tool import connected_session, payload_of

    root = tmp_path / "app"
    shutil.copytree(SAMPLES / "node_express_secrets", root)
    before = digest(root)
    async with connected_session() as client:
        plain = payload_of(await client.call_tool(
            "prodpilot_fix_instruction", {"project_path": str(root), "rule_id": "BLD-002"}))
        dry = payload_of(await client.call_tool(
            "prodpilot_fix_instruction",
            {"project_path": str(root), "rule_id": "BLD-002", "dry_run": True}))
    assert "preview" not in plain and plain["ok"] is True
    assert dry["ok"] is True and dry["dry_run"] is True and dry["fix"] == plain["fix"]
    assert dry["preview"]["diffs"][0]["diff"].startswith("--- /dev/null")
    assert digest(root) == before
