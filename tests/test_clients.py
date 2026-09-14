"""Every tool, reached the way each IDE reaches it.

Phase 1 proved the transport with the first two tools. This proves it again for
every tool the server exposes today, and it starts the server with the exact
command a client's committed config file names, not with a command written
here. If that config drifts from what works, these tests fail.

Each client entry is the file its IDE reads and the key its servers sit under.
Only the files committed to this repository are listed; a client configured
outside the repository has nothing here to test.

Every call is safe to repeat. The fix tools read the project and change
nothing, and the deploy tool is only ever given a project that fails the
scoring gate, so it stops before pre-flight and never reaches GitHub or Render.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

from prodpilot import __version__
from prodpilot.server import (
    DEPLOY_TOOL_NAME,
    DETECT_STACK_TOOL_NAME,
    FIX_APPLIED_TOOL_NAME,
    FIX_INSTRUCTION_TOOL_NAME,
    PING_TOOL_NAME,
    SERVER_NAME,
)

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "tests" / "samples"

# Client name, the config file it reads, and the key holding its servers.
CLIENTS = {
    "vscode": (".vscode/mcp.json", "servers"),
}

TOOLS = {
    PING_TOOL_NAME,
    DETECT_STACK_TOOL_NAME,
    FIX_INSTRUCTION_TOOL_NAME,
    FIX_APPLIED_TOOL_NAME,
    DEPLOY_TOOL_NAME,
}

# The Node.js with Express blueprint is the largest response the server gives:
# 28 items, about 12 KB on the wire, measured over every tool and sample.
LARGEST = SAMPLES / "node_express_insecure"
BLUEPRINT_ITEMS = 28


def launch(client: str) -> StdioServerParameters:
    """The server's launch command, read from the client's own config file."""
    path, key = CLIENTS[client]
    entry = json.loads((REPO / path).read_text(encoding="utf-8"))[key][SERVER_NAME]
    command = entry["command"].replace("${workspaceFolder}", str(REPO))
    if not Path(command).is_file():
        pytest.skip(f"{path} names {command}, which this checkout does not have")
    return StdioServerParameters(command=command, args=entry.get("args", []), cwd=str(REPO))


@asynccontextmanager
async def session(client: str):
    """Start the server as the client would and complete the handshake."""
    async with stdio_client(launch(client)) as (read, write):
        async with ClientSession(read, write) as opened:
            await opened.initialize()
            yield opened


async def call(opened: ClientSession, tool: str, args: dict) -> tuple[dict, int]:
    """Call a tool and return its payload and the size of the whole result."""
    result = await opened.call_tool(tool, args)
    assert result.is_error is False
    text = result.content[0].text
    wire = len(result.model_dump_json(by_alias=True, exclude_none=True).encode())
    return json.loads(text), wire


pytestmark = pytest.mark.parametrize("client", sorted(CLIENTS))


async def test_the_config_command_completes_the_handshake(client) -> None:
    async with session(client) as opened:
        assert opened.server_info.name == SERVER_NAME
        assert opened.server_info.version == __version__


async def test_every_tool_is_discovered(client) -> None:
    async with session(client) as opened:
        listed = await opened.list_tools()

        assert {tool.name for tool in listed.tools} == TOOLS
        assert all(tool.description for tool in listed.tools)


async def test_ping_answers(client) -> None:
    async with session(client) as opened:
        payload, _ = await call(opened, PING_TOOL_NAME, {})

        assert payload["status"] == "ok"
        assert payload["transport"] == "stdio"


async def test_the_largest_response_arrives_whole(client) -> None:
    async with session(client) as opened:
        payload, wire = await call(opened, DETECT_STACK_TOOL_NAME, {"project_path": str(LARGEST)})

        blueprint = payload["blueprint"]
        listed = sum(len(blueprint[part]) for part in
                     ("required_files", "required_configs", "required_code_patterns"))
        assert payload["detection"]["stack"] == "node_express"
        assert blueprint["item_count"] == listed == BLUEPRINT_ITEMS
        assert 10_000 < wire < 16_000


async def test_a_static_fix_comes_back_as_a_content_contract(client) -> None:
    project = str(SAMPLES / "node_express_insecure")
    async with session(client) as opened:
        payload, _ = await call(opened, FIX_INSTRUCTION_TOOL_NAME,
                                {"project_path": project, "rule_id": "SEC-002"})

        fix = payload["fix"]
        assert payload["ok"] is True
        assert (fix["fix_type"], fix["form"]) == ("STATIC", "content")
        assert fix["contract"]["action"] == "insert_after"
        assert "helmet" in fix["contract"]["content"]


async def test_a_delegated_fix_comes_back_as_a_constraint_contract(client) -> None:
    project = str(SAMPLES / "react_vite_ready")
    async with session(client) as opened:
        payload, _ = await call(opened, FIX_INSTRUCTION_TOOL_NAME,
                                {"project_path": project, "rule_id": "STR-003"})

        fix = payload["fix"]
        contract = fix["contract"]
        assert payload["ok"] is True
        assert (fix["fix_type"], fix["form"]) == ("DYNAMIC-DELEGATED", "constraint")
        assert contract["action"] == "author_within_constraint"
        assert contract["requirement"] and contract["boundary"] and contract["forbidden"]
        assert "content" not in contract


async def test_a_reported_fix_is_answered_by_the_checker(client) -> None:
    project = str(SAMPLES / "react_vite_ready")
    async with session(client) as opened:
        payload, _ = await call(opened, FIX_APPLIED_TOOL_NAME,
                                {"project_path": project, "rule_id": "STR-003",
                                 "applied": True, "summary": "claimed, not made"})

        assert payload["outcome"] == "unresolved"
        assert payload["verified"] is False
        assert payload["claim"]["applied"] is True


async def test_deploy_stops_at_the_gate_for_a_failing_project(client) -> None:
    project = str(SAMPLES / "node_express_insecure")
    async with session(client) as opened:
        payload, _ = await call(opened, DEPLOY_TOOL_NAME, {"project_path": project})

        assert payload["ok"] is False
        assert payload["failed_stage"] == "scoring gate"
        assert [step["ran"] for step in payload["steps"]][1:] == [False] * 8
        assert payload["url"] == ""
