"""MCP server construction for ProdPilot.

Hosts the tools registered so far. Module 1.1 contributes the connectivity
check. Module 1.2 contributes stack detection and blueprint loading for Layer 0.
Module 3.5 contributes the two fix loop tools. Local config and secrets handling
are module 1.3 and are not referenced here.

Transport is stdio, per Section 8 of the Complete Solution Document. ProdPilot
is a standard MCP server with no IDE-specific code, so every MCP client reaches
it the same way.

The fix loop takes two tools rather than one because an MCP server cannot call
the client. Section 5.3 describes the instruction travelling to the agent and
the agent applying one atomic change, and over MCP that is the agent asking for
the instruction and then reporting back, so each direction is its own tool.

Verification is module 3.6 and does not exist yet. build_server takes it as an
argument with no default: with nothing supplied, the tool that reports an
applied fix answers with an explicit error instead of an outcome. Section 5.3
forbids trusting the agent's own report, so an unwired verifier has to be
visible rather than quietly generous.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server import MCPServer

from prodpilot import __version__, audit, dispatch
from prodpilot.constraints import ContractError
from prodpilot.detection import DetectionError, detect_stack
from prodpilot.dispatch import Claim, DispatchError, Fixer, Verify
from prodpilot.extraction import ExtractError
from prodpilot.templates import TemplateError

logger = logging.getLogger(__name__)

SERVER_NAME = "prodpilot"

SERVER_INSTRUCTIONS = (
    "ProdPilot audits a project for production readiness, drives the fixes "
    "through your agent, and deploys the result. This build exposes a "
    "connectivity check, stack detection with the matching production "
    "blueprint, and the two fix loop tools: ask for the fix instruction for a "
    "failing rule, apply exactly what it says, then report back. Deployment is "
    "not wired yet."
)

PING_TOOL_NAME = "prodpilot_ping"
DETECT_STACK_TOOL_NAME = "prodpilot_detect_stack"
FIX_INSTRUCTION_TOOL_NAME = "prodpilot_fix_instruction"
FIX_APPLIED_TOOL_NAME = "prodpilot_fix_applied"

# Returned when the agent reports a fix but no verifier is wired. Not an
# outcome, because this build cannot tell whether the fix worked.
NO_VERIFIER = (
    "independent verification is not wired, so no outcome can be given. "
    "The agent's report alone is never an outcome."
)

# The fix producing modules each refuse in their own way. All three refusals
# mean the same thing to a caller: no instruction, and this rule needs a person.
FIX_ERRORS = (DispatchError, TemplateError, ExtractError, ContractError)


def build_server(verify: Verify | None = None) -> MCPServer:
    """Create the ProdPilot MCP server with its tools registered.

    verify is module 3.6. Passing None leaves the fix report tool unable to
    give an outcome, which is the honest state of this build.
    """
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
    )
    register_tools(server, verify)
    logger.info("built MCP server %s version %s", SERVER_NAME, __version__)
    return server


def register_tools(server: MCPServer, verify: Verify | None = None) -> None:
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

    @server.tool(
        name=FIX_INSTRUCTION_TOOL_NAME,
        title="Get the fix instruction for one failing rule",
        description=(
            "Return the fix contract for one rule that the audit reports as "
            "failing. The form depends on the rule. A content contract carries "
            "the exact change to make, with the file, the anchor, and the "
            "content, and must be applied exactly as given with no other edit. "
            "A constraint contract carries a requirement, a boundary, and a "
            "list of things that must not change, and you author the minimal "
            "change yourself within that boundary. Reads the project and "
            "changes nothing on disk. After applying the change, report it "
            "with prodpilot_fix_applied."
        ),
    )
    def prodpilot_fix_instruction(project_path: str, rule_id: str) -> dict[str, Any]:
        """Render the contract for rule_id against the project at project_path.

        project_path is an absolute or user-relative path to the project root.
        rule_id is the identifier the audit reported, for example SEC-002.
        """
        logger.info("%s called for %s in %s", FIX_INSTRUCTION_TOOL_NAME, rule_id, project_path)
        try:
            report = audit.run(project_path)
        except (OSError, ValueError) as exc:
            logger.warning("%s could not audit: %s", FIX_INSTRUCTION_TOOL_NAME, exc)
            return {"ok": False, "error": str(exc), "fix": None}

        try:
            issue = dispatch.failing(report, rule_id)
            fix = dispatch.instruct(project_path, issue)
        except FIX_ERRORS as exc:
            logger.warning("%s refused %s: %s", FIX_INSTRUCTION_TOOL_NAME, rule_id, exc)
            return {"ok": False, "error": str(exc), "fix": None}

        return {"ok": True, "error": None, "fix": fix.to_dict()}

    @server.tool(
        name=FIX_APPLIED_TOOL_NAME,
        title="Report an applied fix and get the verified outcome",
        description=(
            "Report that you have applied the fix for one rule, and receive the "
            "outcome. Your report is recorded but is never the outcome: "
            "ProdPilot re-runs that rule's own checker against the project and "
            "answers from what the checker finds. Returns resolved when the "
            "rule now passes, unresolved when it still fails and the fix should "
            "be attempted again, or blocked when the rule cannot be attempted "
            "and needs a person."
        ),
    )
    def prodpilot_fix_applied(
        project_path: str,
        rule_id: str,
        applied: bool,
        summary: str = "",
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Verify rule_id independently and return the outcome.

        applied and summary are your own report of what you changed. They are
        recorded next to the verifier's answer and do not affect it.
        """
        logger.info("%s called for %s in %s", FIX_APPLIED_TOOL_NAME, rule_id, project_path)
        claim = Claim(rule_id=rule_id, applied=applied, summary=summary)
        if verify is None:
            logger.warning("%s has no verifier wired", FIX_APPLIED_TOOL_NAME)
            return {
                "ok": False,
                "error": NO_VERIFIER,
                "outcome": None,
                "verified": None,
                "claim": claim.to_dict(),
            }

        try:
            report = audit.run(project_path)
            issue = dispatch.failing(report, rule_id)
        except (OSError, ValueError, DispatchError) as exc:
            logger.warning("%s could not read %s: %s", FIX_APPLIED_TOOL_NAME, rule_id, exc)
            return {
                "ok": False,
                "error": str(exc),
                "outcome": None,
                "verified": None,
                "claim": claim.to_dict(),
            }

        # The agent has already acted, so the agent seam simply hands back the
        # report it just sent. Everything after that is the ordinary resolve
        # step, which renders the contract and then asks the verifier.
        fixer = Fixer(project_path, lambda fix: claim, verify)
        step = fixer.resolve(issue, attempt)
        return {
            "ok": True,
            "error": None,
            "rule_id": rule_id,
            "attempt": attempt,
            "outcome": step.outcome.value,
            "detail": step.detail,
            "verified": fixer.verdicts[-1] if fixer.verdicts else None,
            "claim": claim.to_dict(),
        }


def run_stdio() -> None:
    """Run the server on the stdio transport until the client disconnects."""
    server = build_server()
    logger.info("serving %s over stdio", SERVER_NAME)
    server.run(transport="stdio")
