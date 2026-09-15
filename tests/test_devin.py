"""The command that connects Devin, which blanks ${workspaceFolder}."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prodpilot import devin
from prodpilot.cli import app

REPO = Path(__file__).resolve().parents[1]


def exe(root: Path) -> Path:
    return root / "tools" / "prodpilot.exe"


def test_the_file_names_the_launcher_by_absolute_path(tmp_path: Path):
    done = devin.connect(tmp_path, command=exe(tmp_path))

    data = json.loads((tmp_path / devin.FILE).read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"prodpilot": {"command": exe(tmp_path).as_posix(),
                                                 "args": ["serve"]}}}
    assert "${" not in done.command
    assert Path(done.command).is_absolute()


def test_other_servers_in_the_file_are_kept(tmp_path: Path):
    path = tmp_path / devin.FILE
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}),
                    encoding="utf-8")

    devin.connect(tmp_path, command=exe(tmp_path))

    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"other", "prodpilot"}
    assert servers["other"] == {"command": "x", "args": []}


def test_writing_twice_leaves_one_entry(tmp_path: Path):
    devin.connect(tmp_path, command=exe(tmp_path))
    devin.connect(tmp_path, command=exe(tmp_path))

    data = json.loads((tmp_path / devin.FILE).read_text(encoding="utf-8"))
    assert list(data["mcpServers"]) == ["prodpilot"]


def test_a_file_that_is_not_json_is_left_alone(tmp_path: Path):
    path = tmp_path / devin.FILE
    path.parent.mkdir()
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(devin.DevinError, match="left as it is"):
        devin.connect(tmp_path, command=exe(tmp_path))
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_a_project_that_is_not_a_directory_is_refused(tmp_path: Path):
    with pytest.raises(devin.DevinError, match="not a directory"):
        devin.connect(tmp_path / "missing", command=exe(tmp_path))


def test_the_launcher_is_the_installed_prodpilot_command():
    found = devin.launcher()

    assert found.is_file()
    assert found.name.lower().startswith("prodpilot")


def test_whether_git_ignores_the_file_is_reported(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    assert devin.connect(tmp_path, command=exe(tmp_path)).ignored is False

    (tmp_path / ".gitignore").write_text(".devin/mcp_config.local.json\n", encoding="utf-8")
    assert devin.connect(tmp_path, command=exe(tmp_path)).ignored is True


def test_this_repository_keeps_the_file_out_of_version_control():
    assert devin.ignored(REPO, REPO / devin.FILE) is True


def test_the_command_writes_the_file(tmp_path: Path):
    result = CliRunner().invoke(app, ["devin", "--project", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Devin will start:" in result.output
    command = json.loads((tmp_path / devin.FILE).read_text(encoding="utf-8"))[
        "mcpServers"]["prodpilot"]["command"]
    assert Path(command).is_file()


def test_the_command_fails_visibly_on_a_bad_file(tmp_path: Path):
    path = tmp_path / devin.FILE
    path.parent.mkdir()
    path.write_text("[]", encoding="utf-8")

    result = CliRunner().invoke(app, ["devin", "--project", str(tmp_path)])

    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "[]"
