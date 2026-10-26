"""The assistant as an MCP server, so any MCP client (Claude Desktop, Copilot Studio, watsonx
Orchestrate, another agent) can use it as a tool.

    uv run python -m app.mcp_server          # stdio transport

Two kinds of tool:

- `ask_dmrb`: the governed path. Runs the whole agent: citations checked against what was
  retrieved, departures flagged, escalation on a failed check. Use this to answer a question.
- The agent's own tools (search_standards, get_clause, the CD 109 equations, check_chart,
  check_limit): for a client building its own workflow. These are the same functions the
  agent calls, with the same argument validation, but the citation gate is the caller's job:
  a raw tool result isn't an answer.

The tools are synchronous (Azure SDK calls), so each call runs in a worker thread and the
server's event loop stays free for other requests.
"""

import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.engine import Mode
from agent.loop import AgentResult
from agent.rag import RagResult
from agent.tools.registry import Tool, run_tool
from app.api import to_response

logger = logging.getLogger(__name__)

ASK = "ask_dmrb"
ASK_DESCRIPTION = (
    "Answer a UK highway design question from the DMRB (CD 109 road geometry, CD 123 "
    "junctions, CG 501 drainage, GG 101). Returns the answer with clause citations checked "
    "against the retrieved text, the governing requirement strength (shall / should / ...), "
    "whether a Departure from Standard is needed, and needs_human_review. If needs_human_review "
    "is true or escalation is non-empty, tell the user an engineer must review the answer."
)

AskFn = Callable[[str, Mode], AgentResult | RagResult]


class AskArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=3, max_length=1000, description="The design question")
    mode: Mode = Field(default="agent", description="agent (default), graph or rag")


def _text(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error
    )


def list_tools(tools: Sequence[Tool]) -> list[types.Tool]:
    listed = [
        types.Tool(name=ASK, description=ASK_DESCRIPTION, input_schema=AskArgs.model_json_schema())
    ]
    listed += [
        types.Tool(
            name=t.name, description=t.description, input_schema=t.args_model.model_json_schema()
        )
        for t in tools
    ]
    return listed


def call_ask(ask: AskFn, arguments: Mapping[str, Any]) -> types.CallToolResult:
    try:
        args = AskArgs.model_validate(arguments)
    except ValidationError as err:
        first = err.errors()[0]
        return _text(f"invalid arguments: {'.'.join(map(str, first['loc']))}: {first['msg']}", True)
    request_id = uuid.uuid4().hex[:12]
    try:
        result = ask(args.question, args.mode)
    except Exception as err:  # noqa: BLE001 - reported to the client, logged here
        logger.exception("MCP ask_dmrb failed (request %s)", request_id)
        return _text(f"the assistant failed ({type(err).__name__}); request id {request_id}", True)
    response = to_response(result, args.mode, request_id)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=response.model_dump_json())],
        structured_content=json.loads(response.model_dump_json()),
    )


def call_tool(
    tools: Mapping[str, Tool], name: str, arguments: Mapping[str, Any]
) -> types.CallToolResult:
    # run_tool turns every bad call into a readable error; only Azure / OpenAI errors raise.
    try:
        call = run_tool(tools, name, json.dumps(dict(arguments)))
    except Exception as err:  # noqa: BLE001 - a service failure, reported not raised
        logger.exception("MCP tool %s failed", name)
        return _text(f"{name} failed calling an Azure service ({type(err).__name__})", True)
    return _text(call.output, is_error=not call.ok)


def create_server(tools: Sequence[Tool], ask: AskFn) -> Server:
    by_name = {t.name: t for t in tools}

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list_tools(tools))

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = params.arguments or {}
        if params.name == ASK:
            return await anyio.to_thread.run_sync(call_ask, ask, arguments)
        return await anyio.to_thread.run_sync(call_tool, by_name, params.name, arguments)

    return Server(
        "dmrb-standards-assistant",
        version="0.1.0",
        instructions=(
            "Answers DMRB highway design questions with checked clause citations. Use ask_dmrb "
            "for answers; the other tools are building blocks. Decision support only: an "
            "engineer confirms against the current standard."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def _serve() -> None:  # pragma: no cover - live Azure clients and real stdio
    from mcp.server.stdio import stdio_server

    from agent.engine import build_live_engine

    engine = build_live_engine()
    server = create_server(engine.tools, engine.answer)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)  # stderr: stdout is the MCP channel
    anyio.run(_serve)
