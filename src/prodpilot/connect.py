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


def vscode(command: str | Path | None = None, code: str | None = None,
           run: Run = subprocess.run) -> Connected:
    """Add ProdPilot to the VS Code user profile through VS Code's own CLI."""
    exe = command_of(command)
    entry = {"name": NAME, "type": "stdio", "command": exe, "args": ["serve"]}
    found = code or shutil.which("code")
    if not found:
        raise ConnectError(
            "VS Code's code command is not on PATH. In VS Code, run MCP: Open User "
            "Configuration and add this under servers: "
            + json.dumps({NAME: {"type": "stdio", "command": exe, "args": ["serve"]}}))
    done = run([found, "--add-mcp", json.dumps(entry)], capture_output=True, text=True,
               check=False)
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()
        raise ConnectError(f"code --add-mcp failed with code {done.returncode}: {said}")
    return Connected("vscode", "the VS Code user profile", exe)
