"""Connect an IDE's agent to ProdPilot, from any project.

The configurations committed in this repository start the server from this
repository's own .venv, which is right for developing ProdPilot and wrong for
anyone who installed it. `prodpilot connect` writes each client's own
configuration, naming the prodpilot command installed on this machine by its
absolute path, in the place that client documents:

- VS Code: the user profile, through VS Code's own `code --add-mcp`.
- Cursor: the global ~/.cursor/mcp.json.
- Devin, formerly Windsurf: the project's .devin/mcp_config.local.json. Devin
  blanks the ${workspaceFolder} other configurations use (docs/compatibility.md,
  module 7.2), and that file holds a path valid on one machine only, so whether
  Git ignores it is reported, decided by git check-ignore as the sealing stage
  decides the same question.

Each keeps any other server a file already lists, and leaves a file it cannot
read untouched, saying why.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NAME = "prodpilot"
CLIENTS = ("vscode", "cursor", "devin")
DEVIN_FILE = Path(".devin") / "mcp_config.local.json"
CURSOR_FILE = Path(".cursor") / "mcp.json"


class ConnectError(Exception):
    """A client could not be connected as asked."""


@dataclass(frozen=True)
class Connected:
    """What was written, where, and for Devin whether Git keeps it out."""

    client: str
    where: str
    command: str
    ignored: bool | None = None  # Devin only; None outside a Git repository


def launcher() -> Path:
    """The prodpilot command installed with this Python, by absolute path."""
    name = "prodpilot.exe" if os.name == "nt" else "prodpilot"
    beside = Path(sysconfig.get_path("scripts")) / name
    if beside.is_file():
        return beside.resolve()
    found = shutil.which(NAME)
    if found:
        return Path(found).resolve()
    raise ConnectError("no prodpilot command is installed; install ProdPilot first")


def ignored(root: Path, path: Path) -> bool | None:
    """Whether Git ignores path in the repository at root, or None outside one."""
    try:
        done = subprocess.run(
            ["git", "check-ignore", "-q", path.relative_to(root).as_posix()],
            cwd=root, capture_output=True, check=False)
    except OSError:
        return None
    if done.returncode == 0:
        return True
    if done.returncode == 1:
        return False
    return None


def merge(path: Path, key: str, entry: dict) -> None:
    """Name ProdPilot under key in the JSON file at path, keeping everything else."""
    data: object = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConnectError(f"{path} is not valid JSON, so it was left as it is: {exc}") from exc
    servers = data.setdefault(key, {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise ConnectError(f"{path} holds no {key} object, so it was left as it is")
    servers[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def command_of(command: str | Path | None) -> str:
    return (Path(command) if command else launcher()).as_posix()


def devin(project: str | Path, command: str | Path | None = None) -> Connected:
    """Write the project's own Devin file."""
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise ConnectError(f"{root} is not a directory")
    exe = command_of(command)
    path = root / DEVIN_FILE
    merge(path, "mcpServers", {"command": exe, "args": ["serve"]})
    return Connected("devin", str(path), exe, ignored(root, path))


def cursor(home: str | Path | None = None, command: str | Path | None = None) -> Connected:
    """Write Cursor's global file, which every project it opens reads."""
    path = Path(home or Path.home()) / CURSOR_FILE
    exe = command_of(command)
    merge(path, "mcpServers", {"command": exe, "args": ["serve"]})
    return Connected("cursor", str(path), exe)


Run = Callable[..., subprocess.CompletedProcess]


# Editors built on VS Code install a code command of their own, and one can
# come first on PATH. On the machine this was written on, Cursor's did: it
# accepted --add-mcp, exited 0 and wrote nothing.
NOT_VSCODE = ("cursor", "windsurf", "devin", "codeium")


def vscode_places() -> list[Path]:
    """Where VS Code's installers put its command line."""
    if os.name == "nt":
        places = []
        if os.environ.get("LOCALAPPDATA"):
            places.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Microsoft VS Code"
                          / "bin" / "code.cmd")
        if os.environ.get("ProgramFiles"):
            places.append(Path(os.environ["ProgramFiles"]) / "Microsoft VS Code" / "bin"
                          / "code.cmd")
        return places
    if sys.platform == "darwin":
        return [Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")]
    return [Path("/usr/share/code/bin/code"), Path("/snap/bin/code")]


def vscode_cli(which: Callable[[str], "str | None"] = shutil.which) -> str | None:
    """VS Code's own command line, never another editor's code command."""
    for place in vscode_places():
        if place.is_file():
            return str(place)
    found = which("code")
    if found and not any(name in found.lower() for name in NOT_VSCODE):
        return found
    return None


def vscode_user() -> Path:
    """VS Code's user settings folder, where --add-mcp writes mcp.json."""
    if os.name == "nt":
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(roaming) / "Code" / "User"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "Code" / "User"


def named_in(user: Path, exe: str) -> Path | None:
    """The VS Code configuration under user that names this ProdPilot, if any.

    The default profile's file first, then any other profile's, since
    --add-mcp writes into the profile in use. VS Code allows comments in the
    file, so one that is not plain JSON is read as text.
    """
    for path in [user / "mcp.json", *sorted(user.glob("profiles/*/mcp.json"))]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            servers = json.loads(text).get("servers") or {}
            if (servers.get(NAME) or {}).get("command") == exe:
                return path
        except (ValueError, AttributeError):
            if f'"{NAME}"' in text and exe in text:
                return path
    return None


def vscode(command: str | Path | None = None, code: str | None = None,
           run: Run = subprocess.run, user: str | Path | None = None) -> Connected:
    """Add ProdPilot to the VS Code user profile through VS Code's own CLI.

    Success is read back from VS Code's own configuration, never taken from the
    exit code alone.
    """
    exe = command_of(command)
    by_hand = ("In VS Code, run MCP: Open User Configuration and add this under servers: "
               + json.dumps({NAME: {"type": "stdio", "command": exe, "args": ["serve"]}}))
    found = code or vscode_cli()
    if not found:
        raise ConnectError(f"VS Code's own code command was not found. {by_hand}")
    entry = {"name": NAME, "type": "stdio", "command": exe, "args": ["serve"]}
    done = run([found, "--add-mcp", json.dumps(entry)], capture_output=True, text=True,
               check=False)
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()
        raise ConnectError(f"{found} --add-mcp failed with code {done.returncode}: {said}")
    target = Path(user) if user else vscode_user()
    written = named_in(target, exe)
    if written is None:
        raise ConnectError(
            f"{found} --add-mcp exited without error, but no VS Code configuration under "
            f"{target} names prodpilot, so {found} may not be VS Code's own command. {by_hand}")
    return Connected("vscode", str(written), exe)
