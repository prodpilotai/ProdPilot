"""End to end check of the stdio transport.

Spawns the server exactly as an MCP client does, as a subprocess speaking
JSON-RPC over stdio, then performs the handshake, lists tools, and calls the
tool. Nothing here inspects the server in-process, because the point is to prove
the transport works the way a real client uses it.

The session is opened inside each test rather than in a fixture. The stdio
client and the client session both hold anyio cancel scopes, and anyio requires
a scope to be exited in the task that entered it. An async generator fixture
hands control back across a task boundary, which trips that check.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

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

REPO_ROOT = Path(__file__).resolve().parents[1]


def server_parameters() -> StdioServerParameters:
    """Launch parameters matching how a client starts the server."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "prodpilot", "serve"],
        cwd=str(REPO_ROOT),
    )


@asynccontextmanager
async def connected_session():
    """Start the server as a subprocess and complete the MCP handshake."""
    async with stdio_client(server_parameters()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            yield client


def payload_of(result) -> dict:
    """Read the tool payload from whichever field the server populated."""
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def test_server_identifies_itself() -> None:
    """The handshake reports the ProdPilot server name and version."""
    async with connected_session() as client:
        info = client.server_info

        assert info is not None
        assert info.name == SERVER_NAME
        assert info.version == __version__


async def test_ping_tool_is_discoverable() -> None:
    """The client can discover the tool without knowing about it in advance."""
    async with connected_session() as client:
        listed = await client.list_tools()

        names = [tool.name for tool in listed.tools]
        assert PING_TOOL_NAME in names


async def test_ping_tool_returns_fixed_payload() -> None:
    """Calling the tool over stdio returns the hardcoded status payload."""
    async with connected_session() as client:
        result = await client.call_tool(PING_TOOL_NAME, {})

        assert result.is_error is False

        payload = payload_of(result)
        assert payload["status"] == "ok"
        assert payload["server"] == SERVER_NAME
        assert payload["version"] == __version__
        assert payload["transport"] == "stdio"


async def test_stdout_carries_only_protocol_frames() -> None:
    """Logs must not reach stdout, which would corrupt the JSON-RPC stream.

    A successful handshake plus tool call over the same connection is the
    evidence: any stray stdout write would desynchronise the framing and the
    client would fail before reaching this assertion.
    """
    async with connected_session() as client:
        listed = await client.list_tools()
        result = await client.call_tool(PING_TOOL_NAME, {})

        assert PING_TOOL_NAME in [tool.name for tool in listed.tools]
        assert result.is_error is False


async def test_each_tool_says_whether_it_changes_anything() -> None:
    """The hints reach a client over the real transport.

    Without them Devin filed all five tools under Write. Only the deploy tool
    acts outside the project's files, pushing to GitHub and creating a Render
    service, so it alone is marked as changing its environment.
    """
    async with connected_session() as client:
        listed = await client.list_tools()

    hints = {tool.name: tool.annotations for tool in listed.tools}
    assert set(hints) == {PING_TOOL_NAME, DETECT_STACK_TOOL_NAME, FIX_INSTRUCTION_TOOL_NAME,
                          FIX_APPLIED_TOOL_NAME, DEPLOY_TOOL_NAME}

    deploy = hints.pop(DEPLOY_TOOL_NAME)
    assert deploy.read_only_hint is False
    assert deploy.destructive_hint is True
    assert deploy.idempotent_hint is False
    assert deploy.open_world_hint is True
    for name, hint in hints.items():
        assert hint.read_only_hint is True, name
        assert hint.open_world_hint is False, name
