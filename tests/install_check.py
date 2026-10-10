"""Checks run against an installed ProdPilot, outside the repository, by CI.

Not a test module: the verify workflow runs it with the Python of a fresh
virtual environment that holds only the installed package.

Usage:
    python install_check.py clean <expected-version> <samples-dir>
    python install_check.py before-upgrade
    python install_check.py after-upgrade <expected-version>

clean          prodpilot --version, doctor, an MCP stdio handshake listing
               exactly five tools, audit and a SARIF report on a sample
before-upgrade stores made-up credentials and connects Cursor, with 1.0.0
after-upgrade  the stored credentials are still found and the command Cursor's
               configuration names still completes the handshake
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

TOOLS = {"prodpilot_ping", "prodpilot_detect_stack", "prodpilot_fix_instruction",
         "prodpilot_fix_applied", "prodpilot_deploy"}


def command() -> str:
    name = "prodpilot.exe" if os.name == "nt" else "prodpilot"
    found = Path(sysconfig.get_path("scripts")) / name
    if not found.is_file():
        raise SystemExit(f"no prodpilot command beside this Python at {found}")
    return str(found)


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run([command(), *args], capture_output=True, text=True, input=stdin,
                          encoding="utf-8", errors="replace", timeout=900)
    print(f"$ prodpilot {' '.join(args)} -> {done.returncode}")
    return done


def check(condition: bool, what: str) -> None:
    print(("ok   " if condition else "FAIL ") + what)
    if not condition:
        raise SystemExit(1)


async def listed(exe: str) -> set[str]:
    params = StdioServerParameters(command=exe, args=["serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            return {t.name for t in (await client.list_tools()).tools}


def clean(version: str, samples: Path) -> None:
    done = run("--version")
    check(done.returncode == 0 and done.stdout.strip() == f"prodpilot {version}",
          f"--version says prodpilot {version}: {done.stdout.strip()!r}")

    done = run("doctor")
    check(done.returncode in (0, 1) and "ProdPilot doctor" in done.stdout,
          "doctor runs and reports, exit 1 being only missing credentials or tools here")

    check(asyncio.run(listed(command())) == TOOLS, "the MCP handshake lists exactly the five tools")

    work = Path(tempfile.mkdtemp(prefix="prodpilot-install-"))
    project = work / "app"
    shutil.copytree(samples / "node_express_gated", project)
    done = run("audit", "--ci", str(project))
    summary = json.loads(done.stdout)
    check(done.returncode == 0 and summary["passed"] is True and summary["score"] == 100,
          f"audit passes the gated sample: exit {done.returncode}, score {summary['score']}")

    out = work / "audit.sarif"
    done = run("report", "--format", "sarif", "--out", str(out), str(samples / "node_express_insecure"))
    log = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    check(done.returncode == 0 and log.get("version") == "2.1.0"
          and len(log["runs"][0]["tool"]["driver"]["rules"]) == 50
          and log["runs"][0]["results"],
          "report writes a SARIF 2.1.0 log with 50 rules and results")


def before_upgrade() -> None:
    done = run("setup", stdin="made-up-github-token\nmade-up-render-key\n")
    check(done.returncode in (0, 1) and "Saved to" in done.stdout, "1.0.0 stores the credentials")
    done = run("connect", "cursor")
    check(done.returncode == 0, f"1.0.0 connects Cursor: {done.stdout.strip()}")


def after_upgrade(version: str) -> None:
    done = run("--version")
    check(done.stdout.strip() == f"prodpilot {version}", f"upgraded to {version}")
    done = run("doctor")
    check("github_token: stored" in done.stdout and "render_api_key: stored" in done.stdout,
          "the credentials 1.0.0 stored are still found")
    config = Path.home() / ".cursor" / "mcp.json"
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["prodpilot"]
    exe = entry["command"]
    check(Path(exe).is_file(), f"Cursor's configuration still names an existing command: {exe}")
    check(asyncio.run(listed(exe)) == TOOLS, "that command serves the five tools after the upgrade")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "clean":
        clean(sys.argv[2], Path(sys.argv[3]).resolve())
    elif mode == "before-upgrade":
        before_upgrade()
    elif mode == "after-upgrade":
        after_upgrade(sys.argv[2])
    else:
        raise SystemExit(f"unknown mode {mode}")
    print("all checks passed")
