"""End to end check of the detection tool over stdio.

Same approach as the 1.1 transport test. The server is spawned as a subprocess
speaking JSON-RPC over stdio and driven through a real client session, so what
is proven here is what an IDE agent would actually get back.

Sample project paths are passed absolute, which is how a client refers to a
workspace folder.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

from prodpilot.blueprint import Stack
from prodpilot.server import DETECT_STACK_TOOL_NAME, PING_TOOL_NAME

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = Path(__file__).resolve().parent / "samples"


def server_parameters() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "prodpilot", "serve"],
        cwd=str(REPO_ROOT),
    )


@asynccontextmanager
async def connected_session():
    async with stdio_client(server_parameters()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            yield client


def payload_of(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def call_detect(client, sample_name: str) -> dict:
    result = await client.call_tool(
        DETECT_STACK_TOOL_NAME, {"project_path": str(SAMPLES / sample_name)}
    )
    assert result.is_error is False
    return payload_of(result)


async def test_detection_tool_is_discoverable_alongside_ping() -> None:
    """Both registered tools are advertised to the client."""
    async with connected_session() as client:
        listed = await client.list_tools()

        names = [tool.name for tool in listed.tools]
        assert PING_TOOL_NAME in names
        assert DETECT_STACK_TOOL_NAME in names


async def test_detection_tool_declares_its_input() -> None:
    """The schema tells the agent the tool takes a project path."""
    async with connected_session() as client:
        listed = await client.list_tools()

        tool = next(t for t in listed.tools if t.name == DETECT_STACK_TOOL_NAME)
        assert "project_path" in tool.input_schema["properties"]


async def test_node_express_sample_over_stdio() -> None:
    async with connected_session() as client:
        payload = await call_detect(client, "node_express_api")

        assert payload["ok"] is True
        assert payload["detection"]["stack"] == Stack.NODE_EXPRESS.value
        assert payload["blueprint"]["stack"] == Stack.NODE_EXPRESS.value
        assert payload["blueprint"]["item_count"] > 0


async def test_react_vite_sample_over_stdio() -> None:
    async with connected_session() as client:
        payload = await call_detect(client, "react_vite_app")

        assert payload["ok"] is True
        assert payload["detection"]["stack"] == Stack.REACT_VITE.value
        assert payload["blueprint"]["stack"] == Stack.REACT_VITE.value
        assert payload["blueprint"]["item_count"] > 0


async def test_unrecognized_sample_over_stdio() -> None:
    """An unsupported project returns a result with no blueprint, not an error."""
    async with connected_session() as client:
        payload = await call_detect(client, "unrecognized_python_service")

        assert payload["ok"] is True
        assert payload["detection"]["stack"] == Stack.UNRECOGNIZED.value
        assert payload["detection"]["is_supported"] is False
        assert payload["blueprint"] is None


async def test_each_supported_sample_gets_a_different_blueprint() -> None:
    """The exit criterion: the right blueprint follows the detected stack."""
    async with connected_session() as client:
        node = await call_detect(client, "node_express_api")
        react = await call_detect(client, "react_vite_app")

        node_ids = {
            item["item_id"]
            for item in node["blueprint"]["required_code_patterns"]
        }
        react_ids = {
            item["item_id"]
            for item in react["blueprint"]["required_code_patterns"]
        }

        assert node_ids
        assert react_ids
        assert node_ids.isdisjoint(react_ids)


async def test_config_file_detection_path_works_over_stdio() -> None:
    """Vite found by config file rather than by dependency still loads a blueprint."""
    async with connected_session() as client:
        payload = await call_detect(client, "react_vite_ts_config")

        assert payload["ok"] is True
        assert payload["detection"]["stack"] == Stack.REACT_VITE.value
        assert "vite.config.ts" in payload["detection"]["evidence"]["config_files_found"]
        assert payload["blueprint"]["item_count"] > 0


async def test_blueprint_reports_domains_that_do_not_apply() -> None:
    """Phase 2 needs to tell an absent domain from an inapplicable one."""
    async with connected_session() as client:
        node = await call_detect(client, "node_express_api")
        react = await call_detect(client, "react_vite_app")

        assert node["blueprint"]["not_applicable_domains"] == []
        assert set(react["blueprint"]["not_applicable_domains"]) == {
            "connectivity",
            "api",
        }


async def test_bad_path_returns_structured_error_not_a_crash() -> None:
    """A missing path must fail explicitly and leave the server usable."""
    async with connected_session() as client:
        result = await client.call_tool(
            DETECT_STACK_TOOL_NAME,
            {"project_path": str(SAMPLES / "no-such-project")},
        )
        payload = payload_of(result)

        assert payload["ok"] is False
        assert payload["detection"] is None
        assert payload["blueprint"] is None
        assert "does not exist" in payload["error"]

        # The connection survives a failed tool call.
        followup = await client.call_tool(PING_TOOL_NAME, {})
        assert followup.is_error is False
