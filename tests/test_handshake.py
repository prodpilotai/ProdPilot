"""The handshake refusal Copilot's agent met in VS Code, and why it is correct.

In module 7.1 the Copilot CLI's first connection to ProdPilot was refused with
-32022. This file pins down what happened and what a client can do about it,
against the real server over real stdio.

The MCP SDK ProdPilot runs on serves two protocol eras and fixes a connection's
era from the client's first request. A client that probes with `server/discover`
at the 2026-07-28 version, gives up waiting while the server is still starting,
and then sends the older `initialize` on the same connection has already made
that connection a 2026-07-28 one, so the SDK refuses the handshake. The refusal
is not a dead end: it is the protocol's negotiation signal, carrying the
versions the server serves, and a client that probes again on the same
connection, as the SDK's own client does, connects.

The probe and the handshake are sent back to back here, which is the order the
server sees them in when the client's probe times out during startup, so the
result does not depend on how fast this machine starts the server.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import mcp_types as types
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from mcp_types import UNSUPPORTED_PROTOCOL_VERSION
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_MODERN_VERSION

from prodpilot.server import PING_TOOL_NAME

REPO = Path(__file__).resolve().parents[1]
TOOLS = 5


@asynccontextmanager
async def connection():
    params = StdioServerParameters(command=sys.executable, args=["-m", "prodpilot", "serve"],
                                   cwd=str(REPO))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as opened:
            yield opened


async def refused(opened: ClientSession) -> MCPError:
    """Probe at the modern version, then send the older handshake before the answer."""
    probe = asyncio.ensure_future(opened.send_discover(LATEST_MODERN_VERSION))
    await asyncio.sleep(0)  # the probe goes out first, as it does from a client
    try:
        with pytest.raises(MCPError) as caught:
            await opened.initialize()
    finally:
        await asyncio.gather(probe, return_exceptions=True)
    return caught.value


async def test_a_handshake_after_a_modern_probe_is_refused_with_the_versions_to_use() -> None:
    async with connection() as opened:
        error = await refused(opened)

    data = types.UnsupportedProtocolVersionErrorData.model_validate(error.error.data)
    assert error.code == UNSUPPORTED_PROTOCOL_VERSION == -32022
    assert LATEST_MODERN_VERSION in data.supported
    assert data.requested in HANDSHAKE_PROTOCOL_VERSIONS


async def test_probing_again_on_the_same_connection_recovers() -> None:
    """What the SDK's own client does with the refusal, and what Copilot's does not."""
    async with connection() as opened:
        error = await refused(opened)
        data = types.UnsupportedProtocolVersionErrorData.model_validate(error.error.data)

        answer = await opened.send_discover(data.supported[-1])
        opened.adopt(types.DiscoverResult.model_validate(answer))
        listed = await opened.list_tools()
        pinged = await opened.call_tool(PING_TOOL_NAME, {})

    assert len(listed.tools) == TOOLS
    assert pinged.is_error is False


async def test_either_era_alone_connects() -> None:
    """Refusal only ever meets a client that mixes the two on one connection."""
    async with connection() as opened:
        legacy = await opened.initialize()
        assert legacy.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS
        assert len((await opened.list_tools()).tools) == TOOLS

    async with connection() as opened:
        answer = await opened.send_discover(LATEST_MODERN_VERSION)
        opened.adopt(types.DiscoverResult.model_validate(answer))
        assert len((await opened.list_tools()).tools) == TOOLS
