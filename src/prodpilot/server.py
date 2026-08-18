"""MCP server construction for ProdPilot.

Hosts the tools registered so far. Module 1.1 contributes the connectivity
check. Module 1.2 contributes stack detection and blueprint loading for Layer 0.
Local config and secrets handling are module 1.3 and are not referenced here.

Transport is stdio, per Section 8 of the Complete Solution Document. ProdPilot
is a standard MCP server with no IDE-specific code, so every MCP client reaches
it the same way.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server import MCPServer

from prodpilot import __version__
from prodpilot.detection import DetectionError, detect_stack

logger = logging.getLogger(__name__)

SERVER_NAME = "prodpilot"

SERVER_INSTRUCTIONS = (
    "ProdPilot audits a project for production readiness, drives the fixes "
    "through your agent, and deploys the result. This build exposes only "
    "prodpilot_ping, a connectivity check. No audit or deployment capability is "
    "wired yet."
)

PING_TOOL_NAME = "prodpilot_ping"
DETECT_STACK_TOOL_NAME = "prodpilot_detect_stack"


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

    @server.tool(
        name=DETECT_STACK_TOOL_NAME,
        title="Detect project stack and load blueprint",
        description=(
            "Identify whether a project directory is Node.js with Express or "
            "React with Vite, and return the production blueprint for the "
            "detected stack. The blueprint lists every file, config, and code "
            "pattern a deployment-ready project must have. Reads package.json "
            "and the project file structure only. Runs no audit and changes "
            "nothing on disk."
        ),
    )
    def prodpilot_detect_stack(project_path: str) -> dict[str, Any]:
        """Classify project_path and return the matching blueprint.

        project_path is an absolute or user-relative path to the project root.
        """
        logger.info("%s called for %s", DETECT_STACK_TOOL_NAME, project_path)
        try:
            result = detect_stack(project_path)
        except DetectionError as exc:
            logger.warning("%s failed: %s", DETECT_STACK_TOOL_NAME, exc)
            return {
                "ok": False,
                "error": str(exc),
                "detection": None,
                "blueprint": None,
            }

        blueprint = result.blueprint()
        return {
            "ok": True,
            "error": None,
            "detection": result.to_dict(),
            "blueprint": blueprint.to_dict() if blueprint else None,
        }


def run_stdio() -> None:
    """Run the server on the stdio transport until the client disconnects."""
    server = build_server()
    logger.info("serving %s over stdio", SERVER_NAME)
    server.run(transport="stdio")
