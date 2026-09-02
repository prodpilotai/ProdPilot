"""End to end check of the fix loop tools over stdio.

Same approach as the 1.1 and 1.2 transport tests. The server is spawned as a
subprocess speaking JSON-RPC over stdio and driven through a real client
session, so what is proven here is what an IDE agent would actually get back.

Two servers are used, on purpose.

The first is the one the CLI launches, with no verifier, because module 3.6 does
not exist. It has to refuse to give an outcome. That refusal is the evidence
that nothing was stubbed true to make the wiring look finished.

The second is tests/wired_server.py, which fills the 3.6 seam with a double
whose answers a test controls through the environment. It proves the whole path
carries a refusal as faithfully as a success, over the real transport.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

from prodpilot.server import (
    DETECT_STACK_TOOL_NAME,
    FIX_APPLIED_TOOL_NAME,
    FIX_INSTRUCTION_TOOL_NAME,
    PING_TOOL_NAME,
)
from wired_server import PASSING

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = Path(__file__).resolve().parent / "samples"
INSECURE = str(SAMPLES / "node_express_insecure")


def server_parameters() -> StdioServerParameters:
    """The server as an MCP client starts it, with no verifier wired."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "prodpilot", "serve"],
        cwd=str(REPO_ROOT),
    )


def wired_parameters(passing: str = "") -> StdioServerParameters:
    """The same server with the 3.6 seam filled by the test double."""
    return StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "wired_server.py")],
        cwd=str(REPO_ROOT),
        env={**os.environ, PASSING: passing},
    )


@asynccontextmanager
async def connected_session(parameters: StdioServerParameters | None = None):
    async with stdio_client(parameters or server_parameters()) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


def payload_of(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def ask(client, rule_id: str, project_path: str = INSECURE) -> dict:
    result = await client.call_tool(
        FIX_INSTRUCTION_TOOL_NAME, {"project_path": project_path, "rule_id": rule_id}
    )
    assert result.is_error is False
    return payload_of(result)


async def report(client, rule_id: str, applied: bool = True, **extra) -> dict:
    result = await client.call_tool(
        FIX_APPLIED_TOOL_NAME,
        {
            "project_path": INSECURE,
            "rule_id": rule_id,
            "applied": applied,
            "summary": "edited the file as instructed",
            **extra,
        },
    )
    assert result.is_error is False
    return payload_of(result)


# --------------------------------------------------------------------------
# registration, alongside the tools already there
# --------------------------------------------------------------------------


async def test_both_fix_tools_are_discoverable_alongside_the_earlier_tools() -> None:
    async with connected_session() as client:
        listed = await client.list_tools()

        names = [tool.name for tool in listed.tools]
        assert PING_TOOL_NAME in names
        assert DETECT_STACK_TOOL_NAME in names
        assert FIX_INSTRUCTION_TOOL_NAME in names
        assert FIX_APPLIED_TOOL_NAME in names


async def test_the_fix_tools_declare_their_inputs() -> None:
    async with connected_session() as client:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}

        asked = tools[FIX_INSTRUCTION_TOOL_NAME].input_schema["properties"]
        assert set(asked) == {"project_path", "rule_id"}

        told = tools[FIX_APPLIED_TOOL_NAME].input_schema["properties"]
        assert {"project_path", "rule_id", "applied"} <= set(told)


def test_the_registration_call_from_1_1_and_1_2_still_works() -> None:
    """The server change is additive: the old one argument call still registers."""
    from mcp.server import MCPServer

    from prodpilot.server import register_tools

    server = MCPServer(name="test", version="0", instructions="")
    register_tools(server)


# --------------------------------------------------------------------------
# both contract forms, over the wire
# --------------------------------------------------------------------------


async def test_a_static_rule_returns_the_content_form_over_stdio() -> None:
    async with connected_session() as client:
        payload = await ask(client, "SEC-002")

        assert payload["ok"] is True
        fix = payload["fix"]
        assert fix["fix_type"] == "STATIC"
        assert fix["form"] == "content"
        assert fix["contract"]["content"].strip()
        assert fix["contract"]["constraint"].startswith("Apply exactly this change")


async def test_a_parametric_rule_returns_the_content_form_over_stdio() -> None:
    """The second producer of the first form, carrying its own fix type."""
    async with connected_session() as client:
        payload = await ask(client, "BLD-001")

        assert payload["ok"] is True
        fix = payload["fix"]
        assert fix["fix_type"] == "DYNAMIC-PARAMETRIC"
        assert fix["form"] == "content"
        assert fix["contract"]["content"].strip()


async def test_a_delegated_rule_returns_the_constraint_form_over_stdio() -> None:
    async with connected_session() as client:
        payload = await ask(client, "STR-001")

        assert payload["ok"] is True
        fix = payload["fix"]
        assert fix["fix_type"] == "DYNAMIC-DELEGATED"
        assert fix["form"] == "constraint"
        assert fix["contract"]["action"] == "author_within_constraint"
        assert fix["contract"]["requirement"].strip()
        assert fix["contract"]["boundary"].strip()
        assert fix["contract"]["forbidden"].strip()
        assert "content" not in fix["contract"]


async def test_the_delegated_contract_carries_no_authored_code() -> None:
    """ProdPilot supplies the boundary, not the text, and the wire proves it."""
    async with connected_session() as client:
        fix = (await ask(client, "STR-002"))["fix"]

        for value in fix["contract"].values():
            assert "require(" not in value
            assert "app.use" not in value


# --------------------------------------------------------------------------
# refusals reach the agent as refusals
# --------------------------------------------------------------------------


async def test_a_rule_the_project_passes_gets_no_instruction() -> None:
    async with connected_session() as client:
        payload = await ask(client, "SEC-002", project_path=str(SAMPLES / "node_express_secure"))

        assert payload["ok"] is False
        assert payload["fix"] is None
        assert "not a failing rule" in payload["error"]

        followup = await client.call_tool(PING_TOOL_NAME, {})
        assert followup.is_error is False


async def test_an_ambiguous_extraction_refuses_over_stdio() -> None:
    """3.3 refuses rather than guessing, and the refusal survives the transport."""
    async with connected_session() as client:
        payload = await ask(client, "BLD-001", project_path=str(SAMPLES / "amb_two_lockfiles"))

        assert payload["ok"] is False
        assert payload["fix"] is None
        assert "package manager is unclear" in payload["error"]


async def test_a_bad_path_returns_a_structured_error_not_a_crash() -> None:
    async with connected_session() as client:
        result = await client.call_tool(
            FIX_INSTRUCTION_TOOL_NAME,
            {"project_path": str(SAMPLES / "no-such-project"), "rule_id": "SEC-002"},
        )
        payload = payload_of(result)

        assert payload["ok"] is False
        assert payload["fix"] is None
        assert payload["error"]

        followup = await client.call_tool(PING_TOOL_NAME, {})
        assert followup.is_error is False


# --------------------------------------------------------------------------
# the agent's report is not an outcome
# --------------------------------------------------------------------------


async def test_the_shipped_server_gives_no_outcome_because_3_6_is_not_built() -> None:
    """The single most important assertion in this file.

    Nothing was stubbed true to make the wiring look complete. With no verifier
    the tool says so and returns no outcome, even though the agent reported a
    successful fix.
    """
    async with connected_session() as client:
        payload = await report(client, "SEC-002", applied=True)

        assert payload["ok"] is False
        assert payload["outcome"] is None
        assert payload["verified"] is None
        assert "verification is not wired" in payload["error"]
        assert payload["claim"]["applied"] is True


async def test_the_unverified_answer_never_says_resolved() -> None:
    async with connected_session() as client:
        for rule_id in ("SEC-002", "BLD-001", "STR-001"):
            payload = await report(client, rule_id, applied=True)

            assert payload["outcome"] != "resolved", rule_id


# --------------------------------------------------------------------------
# the full path, with the 3.6 seam filled by a double
# --------------------------------------------------------------------------


async def test_a_verified_pass_resolves_over_stdio() -> None:
    async with connected_session(wired_parameters("SEC-002")) as client:
        payload = await report(client, "SEC-002", applied=True)

        assert payload["ok"] is True
        assert payload["outcome"] == "resolved"
        assert payload["verified"] is True
        assert payload["rule_id"] == "SEC-002"


async def test_a_claim_of_success_the_verifier_rejects_is_not_resolved() -> None:
    """Section 5.3 over the real transport: the claim loses to the checker."""
    async with connected_session(wired_parameters("")) as client:
        payload = await report(client, "SEC-002", applied=True)

        assert payload["ok"] is True
        assert payload["claim"]["applied"] is True
        assert payload["verified"] is False
        assert payload["outcome"] == "unresolved"


async def test_a_claim_of_failure_the_verifier_accepts_is_resolved() -> None:
    """The other direction, so the claim is ignored rather than inverted."""
    async with connected_session(wired_parameters("SEC-002")) as client:
        payload = await report(client, "SEC-002", applied=False)

        assert payload["claim"]["applied"] is False
        assert payload["verified"] is True
        assert payload["outcome"] == "resolved"


async def test_the_verifier_answers_per_rule_not_per_call() -> None:
    """One rule passing must not carry another rule to resolved."""
    async with connected_session(wired_parameters("STR-001")) as client:
        delegated = await report(client, "STR-001", applied=True)
        static = await report(client, "SEC-002", applied=True)

        assert delegated["outcome"] == "resolved"
        assert static["outcome"] == "unresolved"


async def test_every_fix_type_completes_the_path_over_stdio() -> None:
    """Issue in, contract out, report back, verified outcome, for all three."""
    async with connected_session(wired_parameters("SEC-002,BLD-001,STR-001")) as client:
        for rule_id, form in (
            ("SEC-002", "content"),
            ("BLD-001", "content"),
            ("STR-001", "constraint"),
        ):
            fix = (await ask(client, rule_id))["fix"]
            assert fix["form"] == form, rule_id

            payload = await report(client, rule_id, applied=True)
            assert payload["outcome"] == "resolved", rule_id
            assert payload["verified"] is True, rule_id


async def test_the_attempt_number_is_carried_back() -> None:
    async with connected_session(wired_parameters("")) as client:
        payload = await report(client, "SEC-002", applied=True, attempt=3)

        assert payload["attempt"] == 3
        assert payload["outcome"] == "unresolved"


async def test_reporting_a_rule_the_project_passes_gives_no_outcome() -> None:
    async with connected_session(wired_parameters("SEC-002")) as client:
        result = await client.call_tool(
            FIX_APPLIED_TOOL_NAME,
            {
                "project_path": str(SAMPLES / "node_express_secure"),
                "rule_id": "SEC-002",
                "applied": True,
            },
        )
        payload = payload_of(result)

        assert payload["ok"] is False
        assert payload["outcome"] is None
        assert "not a failing rule" in payload["error"]
