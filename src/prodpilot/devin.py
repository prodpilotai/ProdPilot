"""Devin's own configuration file for one project.

Devin, the IDE formerly distributed as Windsurf, imports the committed VS Code
and Cursor configurations but treats ${workspaceFolder} as an unset
environment variable and blanks it, so the server never starts from them
(docs/compatibility.md, module 7.2). It connects from its own highest
precedence file, .devin/mcp_config.local.json, when that file names the
launcher by its absolute path. This writes that file.

The path is valid on one machine only, so the file must stay out of version
control. Whether it does is reported, decided by git check-ignore, which is
how the sealing stage decides the same question.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
from dataclasses import dataclass
from pathlib import Path

FILE = Path(".devin") / "mcp_config.local.json"
NAME = "prodpilot"


class DevinError(Exception):
    """The file could not be written as asked."""


@dataclass(frozen=True)
class Written:
    """What was written, and whether Git will keep it out of the repository."""

    path: Path
    command: str
    ignored: bool | None  # None when the project is not a Git repository


def launcher() -> Path:
    """The prodpilot command installed with this Python, by absolute path."""
    name = "prodpilot.exe" if os.name == "nt" else "prodpilot"
    beside = Path(sysconfig.get_path("scripts")) / name
    if beside.is_file():
        return beside.resolve()
    found = shutil.which(NAME)
    if found:
        return Path(found).resolve()
    raise DevinError("no prodpilot command is installed; install ProdPilot first, "
                     "for example with pip install -e .")


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


def connect(project: str | Path, command: str | Path | None = None) -> Written:
    """Name ProdPilot in the project's Devin file, keeping any other server it lists."""
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise DevinError(f"{root} is not a directory")
    exe = Path(command) if command else launcher()
    path = root / FILE

    data: object = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DevinError(f"{path} is not valid JSON, so it was left as it is: {exc}") from exc
    servers = data.setdefault("mcpServers", {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise DevinError(f"{path} holds no mcpServers object, so it was left as it is")

    servers[NAME] = {"command": exe.as_posix(), "args": ["serve"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return Written(path, exe.as_posix(), ignored(root, path))
