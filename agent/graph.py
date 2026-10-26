"""The Phase 4 agent as a LangGraph state graph: same tools, gate and limits as agent/loop.py.

    uv run python -m agent.graph "What superelevation for a 510 m radius at 100 kph, rural?"
    uv run python -m agent.graph --mermaid      # print the graph as a Mermaid diagram

agent/loop.py is a plain while-loop; this is the same control flow as explicit nodes and
edges, so the two can be compared on the same questions:

    START -> model -> tools -> model ...          (tool calls)
             model -> check -> END                (answer passed the citation gate)
                      check -> model              (one retry after a failed check)
             any step past the limits -> limit -> review -> END
             any failed answer ->                  review -> END

`review` is where an engineer takes over. Built with a checkpointer and
`pause_for_review=True`, the graph stops *before* that node and keeps its state; a reviewer
can inspect it and resume. That is Phase 5's human-in-the-loop, which LangGraph gives
for free and a while-loop doesn't.

Reused, not reimplemented: the tools and run_tool (registry), the citation gate, the prompt
and output contract, parse_kwargs, and AgentResult, so both versions return the same result.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from pydantic import ValidationError

from agent.answer import Answer
from agent.citations import CitationCheck, verify_citations
from agent.loop import (
    MAX_STEPS,
    TIME_BUDGET_S,
    AgentResult,
    ParseAPI,
    RunRecord,
    agent_instructions,
    build_result,
    function_calls,
    parse_kwargs,
)
from agent.rag import MAX_ATTEMPTS, NO_ANSWER, RETRY_INSTRUCTION, Usage, retry_message
from agent.tools.registry import Tool, ToolCall, run_tool
from agent.trace import NullTrace, Trace

Route = Literal["tools", "check", "model", "review", "end"]


class GraphState(TypedDict, total=False):
    """Everything a run carries between nodes. Nodes return only the keys they change."""

    question: str
    started: float  # this process's monotonic clock: budget checks only hold within one process
    step: int
    pending: list[dict]  # input for the next model call
    previous_id: str | None
    requested: list[dict]  # function calls the model just asked for
    # Answers and checks are kept as JSON dicts, not Pydantic objects: the checkpoint
    # serialiser will refuse unregistered classes in a future LangGraph release.
    reply: dict | None  # the answer the model just gave
    route: Route
    evidence: tuple[str, ...]
    calls: tuple[dict, ...]
    answer: dict | None  # latest answer that parsed
    check: dict | None
    answers_tried: int
    reasons: tuple[str, ...]
    input_tokens: int
    output_tokens: int


def _model_node(
    responses: ParseAPI, model: str, instructions: str, tools: dict[str, Tool], trace: Trace
) -> Callable[[GraphState], GraphState]:
    def model_node(state: GraphState) -> GraphState:
        step = state["step"] + 1
        args = parse_kwargs(
            model, instructions, tools, state["pending"], state.get("previous_id"), step
        )
        started = time.monotonic()
        try:
            response = responses.parse(**args)
        except ValidationError as err:
            # As in loop.py: resend the tool outputs with only the latest correction.
            problem = f"Your output was not a valid answer ({err.errors()[0]['msg']})."
            trace.event("model", step=step, error=problem)
            outputs = [i for i in state["pending"] if i.get("type") == "function_call_output"]
            correction = {"role": "user", "content": f"{problem}\n{RETRY_INSTRUCTION}"}
            return {
                "step": step,
                "pending": [*outputs, correction],
                "reasons": (*state["reasons"], f"Step {step}: {problem}"),
                "route": "model",
            }
        usage = Usage()
        usage.add(response)
        calls, parsed = function_calls(response), response.output_parsed
        reported = getattr(response, "usage", None)
        trace.event(
            "model",
            step=step,
            seconds=round(time.monotonic() - started, 3),
            tool_calls=[c.name for c in calls],
            input_tokens=getattr(reported, "input_tokens", None),
            output_tokens=getattr(reported, "output_tokens", None),
        )
        update: GraphState = {
            "step": step,
            "previous_id": response.id,
            "input_tokens": state["input_tokens"] + usage.input_tokens,
            "output_tokens": state["output_tokens"] + usage.output_tokens,
            "requested": [
                {"name": c.name, "arguments": c.arguments, "call_id": c.call_id} for c in calls
            ],
            "reply": None if calls or parsed is None else parsed.model_dump(mode="json"),
        }
        if calls:
            return update | {"route": "tools"}
        if parsed is None:
            return update | {
                "reasons": (*state["reasons"], f"Step {step}: {NO_ANSWER}"),
                "route": "review",
            }
        return update | {"route": "check"}

    return model_node


def _tools_node(tools: dict[str, Tool], trace: Trace) -> Callable[[GraphState], GraphState]:
    def tools_node(state: GraphState) -> GraphState:
        records: list[ToolCall] = [
            run_tool(tools, call["name"], call["arguments"]) for call in state["requested"]
        ]
        for r in records:
            trace.event(
                "tool",
                step=state["step"],
                name=r.name,
                arguments=r.arguments,
                ok=r.ok,
                evidence=r.evidence,
                seconds=round(r.seconds, 3),
                output=r.output,
            )
        new_ids = (i for r in records for i in r.evidence)
        return {
            "pending": [
                {"type": "function_call_output", "call_id": call["call_id"], "output": r.output}
                for call, r in zip(state["requested"], records, strict=True)
            ],
            "evidence": tuple(dict.fromkeys((*state["evidence"], *new_ids))),
            "calls": (
                *state["calls"],
                *(
                    {
                        "name": r.name,
                        "arguments": r.arguments,
                        "ok": r.ok,
                        "seconds": round(r.seconds, 3),
                    }
                    for r in records
                ),
            ),
            "route": "model",
        }

    return tools_node


def _check_node(trace: Trace) -> Callable[[GraphState], GraphState]:
    def check_node(state: GraphState) -> GraphState:
        answer = Answer.model_validate(state["reply"])
        check = verify_citations(answer, state["evidence"])
        tried = state["answers_tried"] + 1
        trace.event(
            "check",
            step=state["step"],
            ok=check.ok,
            reasons=check.reasons,
            answer=answer.model_dump(mode="json"),
        )
        update: GraphState = {
            "answer": state["reply"],
            "check": check.model_dump(mode="json"),
            "answers_tried": tried,
        }
        if check.ok:
            return update | {"reasons": (), "route": "end"}
        reason = f"Answer {tried}: citation check failed: {' '.join(check.reasons)}"
        reasons = (*state["reasons"], reason)
        if tried >= MAX_ATTEMPTS:
            return update | {"reasons": reasons, "route": "review"}
        return update | {"reasons": reasons, "pending": [retry_message(check)], "route": "model"}

    return check_node


def _limit_node(state: GraphState) -> GraphState:
    if state["step"] >= MAX_STEPS:
        reason = f"Stopped at the {MAX_STEPS}-step limit without a checked answer."
    else:
        reason = f"Stopped at the {TIME_BUDGET_S:.0f}s time budget."
    return {"reasons": (*state["reasons"], reason), "route": "review"}


def _review_node(state: GraphState) -> GraphState:
    # Reached only when the run escalated. With pause_for_review the graph stops before this
    # node; resuming runs it and ends. Phase 5 puts the engineer's verdict here.
    return {"route": "end"}


def _guarded(clock: Callable[[], float]) -> Callable[[GraphState], str]:
    """Where to go after a node, with the step and time limits checked before any model call."""

    def route(state: GraphState) -> str:
        nxt = state["route"]
        if nxt != "model":
            return END if nxt == "end" else nxt
        over_time = clock() - state["started"] > TIME_BUDGET_S
        return "limit" if state["step"] >= MAX_STEPS or over_time else "model"

    return route


def build_graph(
    tools: Sequence[Tool],
    responses: ParseAPI,
    model: str,
    system_prompt: str,
    trace: Trace | None = None,
    clock: Callable[[], float] = time.monotonic,
    checkpointer: Checkpointer = None,
    pause_for_review: bool = False,
) -> CompiledStateGraph:
    if pause_for_review and checkpointer is None:
        raise ValueError("pause_for_review needs a checkpointer to keep the paused state")
    trace = trace or NullTrace()
    by_name = {t.name: t for t in tools}
    graph = StateGraph(GraphState)
    graph.add_node(
        "model", _model_node(responses, model, agent_instructions(system_prompt), by_name, trace)
    )
    graph.add_node("tools", _tools_node(by_name, trace))
    graph.add_node("check", _check_node(trace))
    graph.add_node("limit", _limit_node)
    graph.add_node("review", _review_node)
    route = _guarded(clock)
    # Guarded from START too, so the time budget is checked before the first call (as loop.py).
    graph.add_conditional_edges(START, route, ["model", "limit"])
    graph.add_conditional_edges("model", route, ["tools", "check", "model", "review", "limit"])
    graph.add_conditional_edges("tools", route, ["model", "limit"])
    graph.add_conditional_edges("check", route, ["model", "review", "limit", END])
    graph.add_edge("limit", "review")
    graph.add_edge("review", END)
    return graph.compile(
        checkpointer=checkpointer, interrupt_before=["review"] if pause_for_review else None
    )


def initial_state(question: str, started: float) -> GraphState:
    return {
        "question": question,
        "started": started,
        "step": 0,
        "pending": [{"role": "user", "content": question}],
        "previous_id": None,
        "evidence": (),
        "calls": (),
        "answer": None,
        "check": None,
        "answers_tried": 0,
        "reasons": (),
        "input_tokens": 0,
        "output_tokens": 0,
        "route": "model",
    }


def to_result(state: GraphState, seconds: float, trace_id: str) -> AgentResult:
    """The same AgentResult as loop.py, so render() and the evals treat both alike."""
    usage = Usage(state["input_tokens"], state["output_tokens"])
    record = RunRecord(
        evidence=dict.fromkeys(state["evidence"]),
        usage=usage,
        answer=Answer.model_validate(state["answer"]) if state.get("answer") else None,
        check=CitationCheck.model_validate(state["check"]) if state.get("check") else None,
        reasons=list(state["reasons"]),
        answers_tried=state["answers_tried"],
    )
    result = build_result(state["question"], record, state["step"], seconds, trace_id)
    # Validated, not model_copy'd: after a checkpoint round trip tuples come back as lists.
    return AgentResult.model_validate(result.model_dump() | {"tool_calls": tuple(state["calls"])})


def run_graph(
    question: str,
    tools: Sequence[Tool],
    responses: ParseAPI,
    model: str,
    system_prompt: str,
    trace: Trace | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    """Answer one question by running the graph to the end."""
    if not question.strip():
        raise ValueError("empty question")
    trace = trace or NullTrace()
    app = build_graph(tools, responses, model, system_prompt, trace, clock)
    started = clock()
    trace.event(
        "start", question=question, model=model, tools=[t.name for t in tools], runner="langgraph"
    )
    # Each model step runs at most 2 nodes (model, then tools or check), plus limit and review.
    final = app.invoke(initial_state(question, started), {"recursion_limit": MAX_STEPS * 3 + 4})
    result = to_result(final, clock() - started, trace.run_id)
    trace.event("end", steps=result.steps, escalation=result.escalation, seconds=result.seconds)
    return result


class _NoModel:
    """Stands in for the model when only the graph's shape is wanted (--mermaid)."""

    def parse(self, **kwargs: Any) -> Any:
        raise RuntimeError("--mermaid draws the graph; it doesn't run it")


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wires live Azure clients
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from openai import OpenAIError

    from agent.loop import render
    from agent.rag import RagError, load_system_prompt
    from agent.tools.registry import build_tools
    from agent.trace import JsonlTrace
    from core.clients import chat_client, embedding_client
    from core.settings import SettingsError, load_settings
    from index.embed import embed_texts

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--mermaid", action="store_true", help="print the graph and exit")
    args = parser.parse_args(argv)
    if args.mermaid:
        print(build_graph([], _NoModel(), "", "").get_graph().draw_mermaid())
        return 0
    if not args.question:
        parser.error("a question is required")
    try:
        settings = load_settings()
        system_prompt = load_system_prompt()
    except (SettingsError, RagError) as err:
        print(f"Error: {err}")
        return 1
    credential = DefaultAzureCredential()
    embeddings = embedding_client(settings, credential).embeddings
    search_client = SearchClient(
        settings.search_endpoint, settings.search_index, credential=credential
    )
    tools = build_tools(
        search_client, lambda q: embed_texts(embeddings, settings.embedding_deployment, [q])[0]
    )
    try:
        result = run_graph(
            args.question,
            tools,
            chat_client(settings, credential).responses,
            settings.chat_deployment,
            system_prompt,
            trace=JsonlTrace(),
        )
    except (AzureError, OpenAIError) as err:
        print(f"Error: Azure call failed ({type(err).__name__}): {str(err).splitlines()[0]}")
        return 1
    print(json.dumps(result.model_dump(mode="json"), indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
