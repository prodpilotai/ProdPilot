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

Module 3.6 supplies the verifier. The tool that reports an applied fix builds
one for the project it was given and answers from what that verifier finds, so
the agent's own report never decides the outcome.

Phase 6 contributes the deploy tool. It runs the whole ProdPush pipeline, from
the scoring gate to a smoke tested Render service with an active CI/CD pipeline,
and reports how far the project got. It creates real infrastructure, so its
description says so and asks the agent to confirm with the developer first.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from prodpilot import __version__, audit, dispatch, prodpush, verify
from prodpilot.constraints import ContractError
from prodpilot.detection import DetectionError, detect_stack
from prodpilot.dispatch import Claim, DispatchError
from prodpilot.extraction import ExtractError
from prodpilot.templates import TemplateError
from prodpilot.verify import VerifyError

logger = logging.getLogger(__name__)

SERVER_NAME = "prodpilot"

SERVER_INSTRUCTIONS = (
    "ProdPilot audits a project for production readiness, drives the fixes "
    "through your agent, and deploys the result. This build exposes a "
    "connectivity check, stack detection with the matching production "
    "blueprint, the two fix loop tools: ask for the fix instruction for a "
    "failing rule, apply exactly what it says, then report back, and the "
    "deploy tool, which takes a project that passes the scoring gate to a "
    "live Render service. Confirm with the developer before deploying."
)

PING_TOOL_NAME = "prodpilot_ping"
DETECT_STACK_TOOL_NAME = "prodpilot_detect_stack"
FIX_INSTRUCTION_TOOL_NAME = "prodpilot_fix_instruction"
FIX_APPLIED_TOOL_NAME = "prodpilot_fix_applied"
DEPLOY_TOOL_NAME = "prodpilot_deploy"

# The fix producing modules each refuse in their own way. All three refusals
# mean the same thing to a caller: no instruction, and this rule needs a person.
FIX_ERRORS = (DispatchError, TemplateError, ExtractError, ContractError)

# Each rule's status when an instruction was handed out, keyed by project and
# rule, so the report of the applied change can be checked for regressions the
# same way dispatch.Fixer checks them in process.
STANDING: dict[tuple[str, str], dict] = {}


def where(project_path: str) -> str:
    return str(Path(project_path).expanduser().resolve())


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
                "ProdPilot MCP server is running. Stack detection, the fix "
                "loop, and deployment are available."
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

        STANDING[(where(project_path), rule_id)] = dispatch.statuses(report)
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

        # The agent has already made its change, so there is nothing to send and
        # nothing to render. The only question left is the one the agent cannot
        # answer, so the rule's own checker is run again and decides.
        try:
            result = verify.check(project_path, rule_id)
        except VerifyError as exc:
            logger.warning("%s cannot verify %s: %s", FIX_APPLIED_TOOL_NAME, rule_id, exc)
            return {
                "ok": False,
                "error": str(exc),
                "outcome": None,
                "verified": None,
                "claim": claim.to_dict(),
            }

        step = dispatch.outcome_of(result)
        logger.info(
            "%s: agent claims applied=%s, checker says %s",
            rule_id,
            applied,
            result.status.value,
        )
        outcome, detail = step.outcome.value, step.detail

        # The checker answers for this rule only. A change that broke a rule
        # which passed when the instruction was handed out is not a fix, so it
        # is blocked and the agent is told to undo it.
        broke: tuple[str, ...] = ()
        before = STANDING.get((where(project_path), rule_id))
        if before is not None:
            after = dispatch.standing(project_path)
            if after is not None:
                broke = dispatch.broken(before, after, rule_id)
        if broke:
            outcome = "blocked"
            detail = (f"the change broke {', '.join(broke)}, which passed before it. "
                      f"Undo the change and leave {rule_id} for manual review.")
            logger.warning("%s: %s", rule_id, detail)
        return {
            "ok": True,
            "error": None,
            "rule_id": rule_id,
            "attempt": attempt,
            "outcome": outcome,
            "detail": detail,
            "verified": result.passed and not broke,
            "regressed": list(broke),
            "verdict": result.to_dict(),
            "claim": claim.to_dict(),
        }

    @server.tool(
        name=DEPLOY_TOOL_NAME,
        title="Deploy a project that passes the scoring gate",
        description=(
            "Run the ProdPush pipeline on a project: the scoring gate, "
            "pre-flight checks, environment sealing, a local Docker build "
            "test, a push of the generated files, a Render deployment, deploy "
            "monitoring, a post-deploy smoke test, and CI/CD wiring. Stops at "
            "the first stage that fails and says which one and why. This "
            "creates a real Render service and pushes to the project's GitHub "
            "repository, so confirm with the developer before calling it. "
            "Commit your own changes first: a working tree holding "
            "uncommitted changes outside ProdPilot's generated files is not "
            "deployed."
        ),
    )
    def prodpilot_deploy(project_path: str, branch: str = "main") -> dict[str, Any]:
        """Take project_path from the gate to a live, wired deployment.

        project_path is an absolute or user-relative path to the project root.
        branch is the branch pushed to and deployed from.
        """
        logger.info("%s called for %s on %s", DEPLOY_TOOL_NAME, project_path, branch)
        shipped = prodpush.run(project_path, branch=branch)
        return {**shipped.to_dict(), "summary": shipped.summary()}


def run_stdio() -> None:
    """Run the server on the stdio transport until the client disconnects."""
    server = build_server()
    logger.info("serving %s over stdio", SERVER_NAME)
    server.run(transport="stdio")
