"""The public interface of ProdPilot: MCP tool schemas and CLI commands.

snapshot() describes the interface of the installed code. compatible() says
whether a newer interface still serves everything an older one offered: nothing
removed or renamed, no default changed, no optional input made required, and
every new input optional.

Usage:
    python tests/interface.py --write   store the 1.0.0 snapshot
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import typer
from mcp import ClientSession, StdioServerParameters, stdio_client
from typer.testing import CliRunner

from prodpilot.cli import app

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "baseline" / "interface_v1_0_0.json"

# Help is rendered at a fixed width and without colour, so the stored text does
# not depend on the terminal the snapshot was taken in.
HELP_ENV = {"COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"}


async def _listed() -> list:
    params = StdioServerParameters(command=sys.executable, args=["-m", "prodpilot", "serve"],
                                   env={**os.environ})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            return (await client.list_tools()).tools


def tools() -> dict[str, dict]:
    """Every MCP tool the server advertises, with its input schema."""
    out = {}
    for tool in asyncio.run(_listed()):
        hints = tool.annotations.model_dump(exclude_none=True) if tool.annotations else {}
        out[tool.name] = {"input_schema": tool.input_schema, "annotations": hints}
    return out


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return repr(value)


def commands() -> dict[str, dict]:
    """Every CLI command with its parameters and rendered help."""
    # Read by attribute rather than by class: Typer 0.21 and later ship their
    # own copy of Click, so the group is not a click.Group there.
    group = typer.main.get_command(app)
    if not hasattr(group, "commands"):
        raise TypeError(f"the CLI is not a command group: {type(group).__name__}")
    runner = CliRunner()
    out = {"": {"params": [], "help": runner.invoke(app, ["--help"], env=HELP_ENV).output}}
    for name, command in sorted(group.commands.items()):
        params = []
        for p in command.params:
            if p.name == "help":
                continue
            params.append({
                "name": p.name,
                "kind": p.param_type_name,
                "opts": list(p.opts),
                "secondary_opts": list(getattr(p, "secondary_opts", [])),
                "required": bool(p.required),
                "default": _plain(p.default),
                "flag": bool(getattr(p, "is_flag", False)),
                "type": p.type.name,
            })
        out[name] = {"params": params,
                     "help": runner.invoke(app, [name, "--help"], env=HELP_ENV).output}
    return out


def snapshot() -> dict:
    return {"tools": tools(), "commands": commands()}


def compatible(old: dict, new: dict) -> list[str]:
    """Why new does not still serve everything old did. Empty when it does."""
    problems: list[str] = []

    for name, was in old["tools"].items():
        now = new["tools"].get(name)
        if now is None:
            problems.append(f"tool {name} was removed or renamed")
            continue
        props_was = was["input_schema"].get("properties", {})
        props_now = now["input_schema"].get("properties", {})
        need_was = set(was["input_schema"].get("required", []))
        need_now = set(now["input_schema"].get("required", []))
        for prop, spec in props_was.items():
            if prop not in props_now:
                problems.append(f"tool {name}: input {prop} was removed or renamed")
                continue
            if spec.get("type") != props_now[prop].get("type"):
                problems.append(f"tool {name}: input {prop} changed type")
            if spec.get("default") != props_now[prop].get("default"):
                problems.append(f"tool {name}: input {prop} changed its default")
            if prop not in need_was and prop in need_now:
                problems.append(f"tool {name}: optional input {prop} became required")
        for prop in props_now:
            if prop not in props_was and prop in need_now:
                problems.append(f"tool {name}: new input {prop} is required")
        if was["annotations"] != now["annotations"]:
            problems.append(f"tool {name}: annotations changed")

    for name, was in old["commands"].items():
        now = new["commands"].get(name)
        label = name or "prodpilot"
        if now is None:
            problems.append(f"command {label} was removed or renamed")
            continue
        current = {p["name"]: p for p in now["params"]}
        for p in was["params"]:
            q = current.get(p["name"])
            if q is None:
                problems.append(f"command {label}: {p['name']} was removed or renamed")
                continue
            for opt in p["opts"] + p["secondary_opts"]:
                if opt not in q["opts"] + q["secondary_opts"]:
                    problems.append(f"command {label}: option {opt} was removed")
                if opt.startswith("-") and opt not in now["help"]:
                    problems.append(f"command {label}: option {opt} is missing from --help")
            if p["default"] != q["default"]:
                problems.append(f"command {label}: {p['name']} changed its default")
            if not p["required"] and q["required"]:
                problems.append(f"command {label}: {p['name']} became required")
            if p["kind"] != q["kind"] or p["flag"] != q["flag"]:
                problems.append(f"command {label}: {p['name']} changed kind")
        known = {p["name"] for p in was["params"]}
        for q in now["params"]:
            if q["name"] not in known and q["required"]:
                problems.append(f"command {label}: new parameter {q['name']} is required")
    return problems


if __name__ == "__main__":
    if "--write" not in sys.argv[1:]:
        print("pass --write to store the snapshot of the installed code")
        sys.exit(2)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snapshot(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
