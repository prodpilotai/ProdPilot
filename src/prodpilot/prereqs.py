"""What ProdPilot needs on the machine besides itself.

The audit parses JavaScript with a vendored acorn run under Node, the gate
builds the project in Docker as stage 3 does, and ProdPush commits and pushes
with git. Each is checked the way ProdPilot uses it, so doctor reports what
would actually fail rather than what happens to be installed.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from prodpilot import buildtest

Which = Callable[[str], "str | None"]
Run = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Need:
    """One thing ProdPilot needs, whether it is there, and what was found."""

    name: str
    ok: bool
    detail: str


def version(tool: str, which: Which = shutil.which, run: Run = subprocess.run) -> str | None:
    """The first line tool --version prints, or None when it is missing or fails."""
    found = which(tool)
    if not found:
        return None
    try:
        done = run([found, "--version"], capture_output=True, text=True, check=False,
                   timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    said = (done.stdout or done.stderr or "").strip().splitlines()
    return said[0] if done.returncode == 0 and said else None


def git(which: Which = shutil.which, run: Run = subprocess.run) -> Need:
    found = version("git", which, run)
    if found:
        return Need("git", True, found)
    return Need("git", False, "not found; ProdPush commits and pushes with git, so install Git")


def node(which: Which = shutil.which, run: Run = subprocess.run) -> Need:
    found = version("node", which, run)
    if found:
        return Need("Node.js", True, found)
    return Need("Node.js", False,
                "not found; the audit parses JavaScript with Node, so install Node.js 20 or later")


def docker(connect: Callable[[], object] = buildtest.client) -> Need:
    """Whether the Docker daemon answers, as the gate and stage 3 need it to."""
    try:
        made = connect()
    except buildtest.BuildUnavailable as exc:
        return Need("Docker", False,
                    f"{exc}; the gate and stage 3 build the project in Docker, so start Docker")
    try:
        found = made.version().get("Version", "")
    except Exception:  # the version is only shown, never decided on
        found = ""
    return Need("Docker", True, f"daemon {found}".strip())


def check() -> list[Need]:
    """Everything ProdPilot needs besides itself, in the order a run uses it."""
    return [git(), node(), docker()]
