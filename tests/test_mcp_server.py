"""The MCP server, driven by a real MCP client in-process. Fake engine and search; no Azure."""

import json

import anyio
from azure.core.exceptions import ServiceRequestError
from mcp import Client
from pydantic import BaseModel, ConfigDict

from agent.tools.registry import Tool, build_tools
from app.mcp_server import ASK, create_server
from tests.test_api import FakeAnswerer, _agent_result
from tests.test_search_tool import FakeSearch, _doc, _embed


def _tools() -> tuple[Tool, ...]:
    return build_tools(FakeSearch([_doc("CD109-4.2")]), _embed)


def _session(coro_fn, tools=None, ask=None):
    server = create_server(
        tools if tools is not None else _tools(), ask or FakeAnswerer(_agent_result())
    )

    async def run():
        async with Client(server) as client:
            return await coro_fn(client)

    return anyio.run(run)


def test_lists_ask_first_then_every_agent_tool_with_its_schema():
    result = _session(lambda c: c.list_tools())
    names = [t.name for t in result.tools]
    assert names[0] == ASK
    assert {"search_standards", "superelevation", "check_limit"} <= set(names)
    superelevation = next(t for t in result.tools if t.name == "superelevation")
    assert "radius_m" in superelevation.input_schema["properties"]


def test_ask_returns_the_checked_answer_with_review_flags():
    ask = FakeAnswerer(_agent_result())
    result = _session(lambda c: c.call_tool(ASK, {"question": "Max motorway gradient?"}), ask=ask)
    assert not result.is_error and ask.calls == [("Max motorway gradient?", "agent")]
    body = json.loads(result.content[0].text)
    assert body == result.structured_content
    assert {"answer", "citations", "needs_human_review", "escalation", "request_id"} <= set(body)


def test_ask_validates_its_arguments():
    result = _session(lambda c: c.call_tool(ASK, {"question": "x", "mode": "agent"}))
    assert result.is_error and "question" in result.content[0].text


def test_ask_failure_is_an_error_result_with_a_request_id_not_a_crash():
    ask = FakeAnswerer(error=RuntimeError("boom"))
    result = _session(lambda c: c.call_tool(ASK, {"question": "Max motorway gradient?"}), ask=ask)
    assert result.is_error and "RuntimeError" in result.content[0].text
    assert "request id" in result.content[0].text and "boom" not in result.content[0].text


def test_an_agent_tool_runs_with_the_same_validation_as_in_the_agent():
    args = {"design_speed_kph": 100, "radius_m": 510, "area": "rural"}
    ok = _session(lambda c: c.call_tool("superelevation", args))
    assert not ok.is_error and json.loads(ok.content[0].text)["value"] == "6.93"
    bad = _session(lambda c: c.call_tool("superelevation", {**args, "client": "x"}))
    assert bad.is_error and "invalid arguments" in bad.content[0].text


def test_unknown_tool_is_an_error_listing_the_tools():
    result = _session(lambda c: c.call_tool("nope", {}))
    assert result.is_error and "no tool named 'nope'" in result.content[0].text


def test_an_azure_failure_in_a_tool_is_reported_not_raised():
    def down(**kwargs):
        raise ServiceRequestError("network")

    class NoArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")

    tools = (Tool("flaky", "a tool whose Azure call fails, for this test only", NoArgs, down),)
    result = _session(lambda c: c.call_tool("flaky", {}), tools=tools)
    assert result.is_error and "ServiceRequestError" in result.content[0].text
