"""End to end check of the fix loop tools over stdio.

Same approach as the 1.1 and 1.2 transport tests. The server is spawned as a
subprocess speaking JSON-RPC over stdio and driven through a real client
session, so what is proven here is what an IDE agent would actually get back.

One server is used, the one the CLI launches, with the real module 3.6 verifier
behind it. Nothing stands in for verification anywhere in this file: an outcome
here comes from the rule's own checker being run again over the files on disk,
which is why the tests can point the tool at a fixed project and a broken one
and expect opposite answers.
"""

from __future__ import annotations

import json
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

from apply import write
from prodpilot.dispatch import Fix, Form
from prodpilot.rules import FixType
from prodpilot.server import (
    DEPLOY_TOOL_NAME,
    DETECT_STACK_TOOL_NAME,
    FIX_APPLIED_TOOL_NAME,
    FIX_INSTRUCTION_TOOL_NAME,
    PING_TOOL_NAME,
)
REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = Path(__file__).resolve().parent / "samples"
INSECURE = str(SAMPLES / "node_express_insecure")


def server_parameters() -> StdioServerParameters:
    """The server exactly as an MCP client starts it."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "prodpilot", "serve"],
        cwd=str(REPO_ROOT),
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


async def report(client, rule_id: str, applied: bool = True,
                 project_path: str = INSECURE, **extra) -> dict:
    result = await client.call_tool(
        FIX_APPLIED_TOOL_NAME,
        {
            "project_path": project_path,
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
# the outcome comes from the checker, not from the report
# --------------------------------------------------------------------------


async def test_a_false_claim_of_success_is_not_resolved_over_stdio() -> None:
    """The single most important assertion in this file.

    The project is untouched and the rule still fails. The agent says it fixed
    it. The tool runs the rule's own checker and answers unresolved.
    """
    async with connected_session() as client:
        payload = await report(client, "SEC-002", applied=True)

        assert payload["ok"] is True
        assert payload["claim"]["applied"] is True
        assert payload["verified"] is False
        assert payload["outcome"] == "unresolved"
        assert "helmet" in payload["verdict"]["reason"]


async def test_a_false_claim_of_success_is_rejected_for_all_three_fix_types() -> None:
    async with connected_session() as client:
        for rule_id in ("SEC-002", "BLD-001", "STR-001"):
            payload = await report(client, rule_id, applied=True)

            assert payload["outcome"] == "unresolved", rule_id
            assert payload["verified"] is False, rule_id


async def test_a_rule_that_genuinely_passes_resolves_over_stdio() -> None:
    """The other direction, so the tool is not simply always refusing."""
    async with connected_session() as client:
        payload = await report(
            client, "SEC-002", applied=True, project_path=str(SAMPLES / "node_express_ready")
        )

        assert payload["outcome"] == "resolved"
        assert payload["verified"] is True


async def test_a_false_claim_of_failure_does_not_hide_a_real_pass() -> None:
    async with connected_session() as client:
        payload = await report(
            client, "SEC-002", applied=False, project_path=str(SAMPLES / "node_express_ready")
        )

        assert payload["claim"]["applied"] is False
        assert payload["outcome"] == "resolved"


async def test_the_verifier_answers_per_rule_not_per_call() -> None:
    """One rule passing must not carry another rule to resolved."""
    ready = str(SAMPLES / "node_express_ready")
    async with connected_session() as client:
        passing = await report(client, "SEC-002", project_path=ready)
        failing = await report(client, "OBS-002", project_path=ready)

        assert passing["outcome"] == "resolved"
        assert failing["outcome"] == "unresolved"


async def test_a_rule_for_the_other_stack_blocks_rather_than_resolving() -> None:
    async with connected_session() as client:
        payload = await report(client, "BLD-007", applied=True)

        assert payload["outcome"] == "blocked"
        assert payload["verified"] is False


async def test_an_unknown_rule_gives_no_outcome() -> None:
    async with connected_session() as client:
        result = await client.call_tool(
            FIX_APPLIED_TOOL_NAME,
            {"project_path": INSECURE, "rule_id": "NOPE-000", "applied": True},
        )
        payload = payload_of(result)

        assert payload["ok"] is False
        assert payload["outcome"] is None
        assert payload["verified"] is None


async def test_the_attempt_number_is_carried_back() -> None:
    async with connected_session() as client:
        payload = await report(client, "SEC-002", applied=True, attempt=3)

        assert payload["attempt"] == 3


# --------------------------------------------------------------------------
# the whole round trip, with a real change on disk
# --------------------------------------------------------------------------


async def test_asking_applying_and_reporting_resolves_a_rule(tmp_path) -> None:
    """Instruction out, file changed, report back, verified outcome in.

    The change is made by executing the contract exactly as written, which is
    what an agent holding it would do. The verdict at the end is the rule's own
    checker reading the file that was just written.
    """
    project = tmp_path / "project"
    shutil.copytree(SAMPLES / "node_express_insecure", project)

    async with connected_session() as client:
        before = await report(client, "BLD-002", applied=False, project_path=str(project))
        assert before["outcome"] == "unresolved"

        fix = (await ask(client, "BLD-002", project_path=str(project)))["fix"]
        write(project, Fix(fix["rule_id"], FixType.STATIC, Form.CONTENT, fix["contract"]))

        after = await report(client, "BLD-002", applied=True, project_path=str(project))

        assert after["outcome"] == "resolved"
        assert after["verified"] is True
        assert (project / ".dockerignore").is_file()


async def test_the_deploy_tool_is_discoverable_and_asks_for_confirmation() -> None:
    async with connected_session() as client:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}

        assert DEPLOY_TOOL_NAME in tools
        props = tools[DEPLOY_TOOL_NAME].input_schema["properties"]
        assert "project_path" in props and "branch" in props
        assert "confirm with the developer" in tools[DEPLOY_TOOL_NAME].description
