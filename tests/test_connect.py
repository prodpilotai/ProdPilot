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


def writes_like_vscode(user: Path, seen: list | None = None):
    """A stand-in for VS Code's CLI that records the call and writes as VS Code does."""
    def run(args, **_):
        if seen is not None:
            seen.append(args)
        entry = json.loads(args[args.index("--add-mcp") + 1])
        name = entry.pop("name")
        user.mkdir(parents=True, exist_ok=True)
        (user / "mcp.json").write_text(json.dumps({"servers": {name: entry}}), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "Added MCP servers: prodpilot", "")
    return run


def test_vscode_is_asked_through_its_own_add_mcp(tmp_path: Path):
    seen: list[list[str]] = []
    user = tmp_path / "User"

    done = connect.vscode(command=exe(tmp_path), code="code", user=user,
                          run=writes_like_vscode(user, seen))

    assert seen[0][:2] == ["code", "--add-mcp"]
    assert json.loads(seen[0][2]) == {"name": "prodpilot", "type": "stdio",
                                      "command": exe(tmp_path).as_posix(), "args": ["serve"]}
    assert done.where == str(user / "mcp.json")


def test_a_code_command_that_writes_nothing_is_not_counted(tmp_path: Path):
    """On this machine the first code on PATH was Cursor's: it exited 0 and
    wrote nothing, and the command reported success."""
    def run(args, **_):
        return subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(connect.ConnectError, match="may not be VS Code's own command"):
        connect.vscode(command=exe(tmp_path), code="code", user=tmp_path / "User", run=run)


def test_vscode_failing_says_so(tmp_path: Path):
    def run(args, **_):
        return subprocess.CompletedProcess(args, 1, "", "bad option")

    with pytest.raises(connect.ConnectError, match="bad option"):
        connect.vscode(command=exe(tmp_path), code="code", user=tmp_path / "User", run=run)


def test_vscode_own_install_is_preferred_over_code_on_path(tmp_path: Path, monkeypatch):
    own = tmp_path / "Microsoft VS Code" / "bin" / "code.cmd"
    own.parent.mkdir(parents=True)
    own.write_text("", encoding="utf-8")
    monkeypatch.setattr(connect, "vscode_places", lambda: [own])

    found = connect.vscode_cli(which=lambda name: "C:/Programs/cursor/codeBin/code.cmd")

    assert found == str(own)


def test_another_editors_code_command_is_never_used(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(connect, "vscode_places", lambda: [])

    for shim in ("C:/Users/me/AppData/Local/Programs/cursor/resources/app/codeBin/code.cmd",
                 "/opt/Windsurf/bin/code", "C:/Programs/Devin/bin/code.cmd"):
        assert connect.vscode_cli(which=lambda name, shim=shim: shim) is None
    assert connect.vscode_cli(which=lambda name: "/usr/bin/code") == "/usr/bin/code"


def test_without_vscode_the_entry_to_add_is_given(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(connect, "vscode_cli", lambda which=None: None)

    with pytest.raises(connect.ConnectError, match="MCP: Open User Configuration") as raised:
        connect.vscode(command=exe(tmp_path))
    assert exe(tmp_path).as_posix() in str(raised.value)


@pytest.mark.skipif(connect.vscode_cli() is None, reason="VS Code is not installed here")
def test_the_real_vscode_cli_adds_prodpilot_to_a_throwaway_profile(tmp_path: Path):
    """Through VS Code's own CLI, pointed at a profile made for this test, so
    the developer's own VS Code settings are never touched."""
    profile = tmp_path / "profile"

    def run(args, **kw):
        return subprocess.run([args[0], "--user-data-dir", str(profile), *args[1:]],
                              timeout=120, **kw)

    done = connect.vscode(command=exe(tmp_path), user=profile / "User", run=run)

    servers = json.loads((profile / "User" / "mcp.json").read_text(encoding="utf-8"))["servers"]
    assert servers["prodpilot"]["command"] == exe(tmp_path).as_posix()
    assert done.where == str(profile / "User" / "mcp.json")


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
