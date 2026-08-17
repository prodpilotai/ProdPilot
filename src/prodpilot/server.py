"""MCP server construction for ProdPilot.

Scope is Phase 1 module 1.1: build the server, register one tool that returns a
fixed response, and expose it over stdio. Stack detection and blueprint loading
are module 1.2. Local config and secrets handling are module 1.3. Neither is
referenced here.

Transport is stdio, per Section 8 of the Complete Solution Document. ProdPilot
is a standard MCP server with no IDE-specific code, so every MCP client reaches
it the same way.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server import MCPServer

from prodpilot import __version__

logger = logging.getLogger(__name__)

SERVER_NAME = "prodpilot"

SERVER_INSTRUCTIONS = (
    "ProdPilot audits a project for production readiness, drives the fixes "
    "through your agent, and deploys the result. This build exposes only "
    "prodpilot_ping, a connectivity check. No audit or deployment capability is "
    "wired yet."
)

PING_TOOL_NAME = "prodpilot_ping"


def build_server() -> MCPServer:
    """Create the ProdPilot MCP server with its tools registered."""
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
    )
    register_tools(server)
    logger.info("built MCP server %s version %s", SERVER_NAME, __version__)
    return server


def register_tools(server: MCPServer) -> None:
    """Register the tools this module owns."""

    @server.tool(
        name=PING_TOOL_NAME,
        title="ProdPilot connectivity check",
        description=(
            "Confirm that the ProdPilot MCP server is running and reachable. "
            "Returns a fixed status payload. Reads no files and analyses no "
            "project."
        ),
    )
    def prodpilot_ping() -> dict[str, Any]:
        """Return the fixed handshake payload. Takes no arguments."""
        logger.info("%s called", PING_TOOL_NAME)
        return {
            "status": "ok",
            "server": SERVER_NAME,
            "version": __version__,
            "transport": "stdio",
            "detail": (
                "ProdPilot MCP server is running. Audit, fix loop, and "
                "deployment tools are not implemented yet."
            ),
        }


def run_stdio() -> None:
    """Run the server on the stdio transport until the client disconnects."""
    server = build_server()
    logger.info("serving %s over stdio", SERVER_NAME)
    server.run(transport="stdio")
