"""Connecting VS Code, Cursor and Devin to the installed ProdPilot."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prodpilot import connect
from prodpilot.cli import app

REPO = Path(__file__).resolve().parents[1]


def exe(root: Path) -> Path:
    return root / "tools" / "prodpilot.exe"


# --------------------------------------------------------------------------
# the launcher
# --------------------------------------------------------------------------


def test_the_launcher_is_the_installed_prodpilot_command():
    found = connect.launcher()

    assert found.is_file()
    assert found.name.lower().startswith("prodpilot")


# --------------------------------------------------------------------------
# Devin, per project
# --------------------------------------------------------------------------


def test_devin_gets_the_launcher_by_absolute_path(tmp_path: Path):
    done = connect.devin(tmp_path, command=exe(tmp_path))

    data = json.loads((tmp_path / connect.DEVIN_FILE).read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"prodpilot": {"command": exe(tmp_path).as_posix(),
                                                 "args": ["serve"]}}}
    assert "${" not in done.command
    assert Path(done.command).is_absolute()


def test_other_servers_in_the_file_are_kept(tmp_path: Path):
    path = tmp_path / connect.DEVIN_FILE
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}),
                    encoding="utf-8")

    connect.devin(tmp_path, command=exe(tmp_path))

    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"other", "prodpilot"}
    assert servers["other"] == {"command": "x", "args": []}


def test_connecting_twice_leaves_one_entry(tmp_path: Path):
    connect.devin(tmp_path, command=exe(tmp_path))
    connect.devin(tmp_path, command=exe(tmp_path))

    data = json.loads((tmp_path / connect.DEVIN_FILE).read_text(encoding="utf-8"))
    assert list(data["mcpServers"]) == ["prodpilot"]


def test_a_file_that_is_not_json_is_left_alone(tmp_path: Path):
    path = tmp_path / connect.DEVIN_FILE
    path.parent.mkdir()
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(connect.ConnectError, match="left as it is"):
        connect.devin(tmp_path, command=exe(tmp_path))
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_a_project_that_is_not_a_directory_is_refused(tmp_path: Path):
    with pytest.raises(connect.ConnectError, match="not a directory"):
        connect.devin(tmp_path / "missing", command=exe(tmp_path))


def test_whether_git_ignores_the_devin_file_is_reported(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    assert connect.devin(tmp_path, command=exe(tmp_path)).ignored is False

    (tmp_path / ".gitignore").write_text(".devin/mcp_config.local.json\n", encoding="utf-8")
    assert connect.devin(tmp_path, command=exe(tmp_path)).ignored is True


def test_this_repository_keeps_the_devin_file_out_of_version_control():
    assert connect.ignored(REPO, REPO / connect.DEVIN_FILE) is True


# --------------------------------------------------------------------------
# Cursor, global
# --------------------------------------------------------------------------


def test_cursor_gets_its_global_file(tmp_path: Path):
    done = connect.cursor(home=tmp_path, command=exe(tmp_path))

    path = tmp_path / ".cursor" / "mcp.json"
    assert done.where == str(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"prodpilot": {"command": exe(tmp_path).as_posix(), "args": ["serve"]}}}


def test_cursor_keeps_the_servers_already_listed(tmp_path: Path):
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"github": {"command": "gh", "args": []}}}),
                    encoding="utf-8")

    connect.cursor(home=tmp_path, command=exe(tmp_path))

    assert set(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]) == {"github",
                                                                               "prodpilot"}


# --------------------------------------------------------------------------
# VS Code, through its own CLI
# --------------------------------------------------------------------------


def test_vscode_is_asked_through_its_own_add_mcp(tmp_path: Path):
    seen: list[list[str]] = []

    def run(args, **_):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    done = connect.vscode(command=exe(tmp_path), code="code", run=run)

    assert seen[0][:2] == ["code", "--add-mcp"]
    assert json.loads(seen[0][2]) == {"name": "prodpilot", "type": "stdio",
                                      "command": exe(tmp_path).as_posix(), "args": ["serve"]}
    assert done.where == "the VS Code user profile"


def test_vscode_failing_says_so(tmp_path: Path):
    def run(args, **_):
        return subprocess.CompletedProcess(args, 1, "", "bad option")

    with pytest.raises(connect.ConnectError, match="bad option"):
        connect.vscode(command=exe(tmp_path), code="code", run=run)


def test_without_the_code_command_the_entry_to_add_is_given(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(connect.shutil, "which", lambda name: None)

    with pytest.raises(connect.ConnectError, match="MCP: Open User Configuration") as raised:
        connect.vscode(command=exe(tmp_path))
    assert exe(tmp_path).as_posix() in str(raised.value)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def test_the_command_connects_devin(tmp_path: Path):
    result = CliRunner().invoke(app, ["connect", "devin", "--project", str(tmp_path)])

    assert result.exit_code == 0, result.output
    command = json.loads((tmp_path / connect.DEVIN_FILE).read_text(encoding="utf-8"))[
        "mcpServers"]["prodpilot"]["command"]
    assert Path(command).is_file()


def test_the_command_connects_cursor_in_the_home_folder(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(app, ["connect", "cursor"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".cursor" / "mcp.json").is_file()


def test_an_unknown_client_is_refused():
    result = CliRunner().invoke(app, ["connect", "notepad"])

    assert result.exit_code == 2
    assert "vscode, cursor, devin" in result.output


def test_the_command_fails_visibly_on_a_bad_file(tmp_path: Path):
    path = tmp_path / connect.DEVIN_FILE
    path.parent.mkdir()
    path.write_text("[]", encoding="utf-8")

    result = CliRunner().invoke(app, ["connect", "devin", "--project", str(tmp_path)])

    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "[]"
