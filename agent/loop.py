"""Phase 4 agent: the model calls tools until it can answer, then the answer is checked.

    uv run python -m agent.loop "What superelevation for a 510 m radius at 100 kph, rural?"

Each step is one model call through the Responses API. If it asks for tools, they run
(agent/tools/registry.py) and their output goes back on the next step; the conversation is
carried by `previous_response_id`, so only new items are sent. When it answers, the citation
gate from Phase 3 runs against everything the tools returned this run, with one retry.

Limits, so a confused model can't loop or run up a bill: at most MAX_STEPS model calls and
TIME_BUDGET_S seconds. Hitting either escalates: the result is flagged for human review with
the reason, exactly like a failed citation check. Every model call, tool call and check is
written to a JSONL trace (agent/trace.py).
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.answer import Answer
from agent.citations import CitationCheck, verify_citations
from agent.rag import (
    FALLBACK_ANSWER,
    MAX_ATTEMPTS,
    NO_ANSWER,
    RETRY_INSTRUCTION,
    Usage,
    load_system_prompt,
    retry_message,
    with_disclaimer,
)
from agent.tools.registry import Tool, ToolCall, run_tool
from agent.trace import NullTrace, Trace

MAX_STEPS = 6  # model calls per question (build guide)
TIME_BUDGET_S = 90.0  # checked before each model call; a call already running finishes

# Appended after the shared output contract, which talks about "clauses given below".
AGENT_RULES = """\
## Tools (agent mode)

- No clauses come with the question: find them with your tools. "The clauses given" in the
  rules above means the clauses your tools have returned in this conversation.
- Start with search_standards. When a clause you've read points to another clause or table
  ("in accordance with Table 2.10"), fetch it with get_clause or get_table.
- Use a calculation tool whenever one fits, and check_limit for every comparison. Quote the
  tool's result and cite the clauses it lists. If no tool fits, the no-calculation rule above
  still applies.
- If a tool needs an input the question doesn't give (rural or urban, for example), say what
  you assumed, or run it for each case and give both results.
- A tool error explains what was wrong with the call; correct it and try again.
- A not_covered answer cites nothing, including clauses you found while checking.
- You have at most {steps} steps including your answer, so search with focus. On the last
  step tools are switched off and you must answer.
"""


class ParseAPI(Protocol):
    def parse(self, **kwargs: Any) -> Any: ...


class AgentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    answer: Answer
    check: CitationCheck | None
    evidence: tuple[str, ...]  # every ID a tool returned: what the answer may cite
    tool_calls: tuple[dict[str, Any], ...]  # name, arguments, ok, seconds
    steps: int
    escalation: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    trace_id: str = ""

    @property
    def needs_human_review(self) -> bool:
        return self.answer.needs_human_review or bool(self.escalation)


def agent_instructions(system_prompt: str) -> str:
    return f"{system_prompt}\n\n{AGENT_RULES.format(steps=MAX_STEPS)}"


@dataclass
class _Run:
    """What one run has gathered so far. Mutable, but private to run_agent."""

    evidence: dict[str, None] = field(default_factory=dict)  # ordered set
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    answer: Answer | None = None
    check: CitationCheck | None = None
    reasons: list[str] = field(default_factory=list)
    answers_tried: int = 0
    previous_id: str | None = None


def _function_calls(response: Any) -> list[Any]:
    return [item for item in response.output or () if item.type == "function_call"]


def _run_calls(
    calls: Sequence[Any], tools: Mapping[str, Tool], run: _Run, step: int, trace: Trace
) -> list[dict]:
    """Run each requested tool; return the outputs to send back."""
    outputs = []
    for call in calls:
        record = run_tool(tools, call.name, call.arguments)
        run.calls.append(record)
        run.evidence.update(dict.fromkeys(record.evidence))
        trace.event(
            "tool",
            step=step,
            name=record.name,
            arguments=record.arguments,
            ok=record.ok,
            evidence=record.evidence,
            seconds=round(record.seconds, 3),
            output=record.output,
        )
        outputs.append(
            {"type": "function_call_output", "call_id": call.call_id, "output": record.output}
        )
    return outputs


def _check_answer(answer: Answer, run: _Run, step: int, trace: Trace) -> list[dict] | None:
    """Check citations. None means stop (passed, or out of retries); else the retry input."""
    run.answer, run.answers_tried = answer, run.answers_tried + 1
    run.check = verify_citations(answer, run.evidence)
    trace.event(
        "check",
        step=step,
        ok=run.check.ok,
        reasons=run.check.reasons,
        answer=answer.model_dump(mode="json"),
    )
    if run.check.ok:
        run.reasons = []
        return None
    run.reasons.append(
        f"Answer {run.answers_tried}: citation check failed: {' '.join(run.check.reasons)}"
    )
    return None if run.answers_tried >= MAX_ATTEMPTS else [retry_message(run.check)]


def _step(
    responses: ParseAPI,
    model: str,
    instructions: str,
    tools: Mapping[str, Tool],
    pending: list[dict],
    run: _Run,
    step: int,
    trace: Trace,
) -> list[dict] | None:
    """One model call and what follows it. Returns the next input, or None to stop."""
    started = time.monotonic()
    try:
        # Azure rejects previous_response_id=null, so it's only sent from step 2.
        chained = {"previous_response_id": run.previous_id} if run.previous_id else {}
        # On the last step tools are switched off, so the model must answer with what it has
        # rather than ask for one more search and leave the run with nothing.
        last = {"tool_choice": "none"} if step == MAX_STEPS else {}
        response = responses.parse(
            model=model,
            instructions=instructions,
            input=pending,
            tools=[t.schema() for t in tools.values()],
            text_format=Answer,
            **chained,
            **last,
        )
    except ValidationError as err:
        # Schema-valid output that breaks Answer's rules (seen live: a blank answer).
        # The SDK raised before returning, so the response is lost and the conversation is
        # still at the previous one: resend this step's input (it may hold tool outputs the
        # API needs) with the correction.
        first = err.errors()[0]
        problem = f"Your output was not a valid answer ({first['msg']})."
        run.reasons.append(f"Step {step}: {problem}")
        trace.event("model", step=step, error=problem)
        # Earlier corrections are dropped, so repeated failures don't pile up. The lost
        # response's tokens can't be counted.
        outputs = [item for item in pending if item.get("type") == "function_call_output"]
        return [*outputs, {"role": "user", "content": f"{problem}\n{RETRY_INSTRUCTION}"}]
    run.usage.add(response)
    run.previous_id = response.id
    calls = _function_calls(response)
    usage = getattr(response, "usage", None)
    trace.event(
        "model",
        step=step,
        seconds=round(time.monotonic() - started, 3),
        tool_calls=[c.name for c in calls],
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
    if calls:
        return _run_calls(calls, tools, run, step, trace)
    if response.output_parsed is None:
        run.reasons.append(f"Step {step}: {NO_ANSWER}")
        return None
    return _check_answer(response.output_parsed, run, step, trace)


def run_agent(
    question: str,
    tools: Sequence[Tool],
    responses: ParseAPI,
    model: str,
    system_prompt: str,
    trace: Trace | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    """Answer one question with tools, within MAX_STEPS and TIME_BUDGET_S."""
    if not question.strip():
        raise ValueError("empty question")
    trace = trace or NullTrace()
    by_name = {t.name: t for t in tools}
    instructions = agent_instructions(system_prompt)
    started, run = clock(), _Run()
    trace.event("start", question=question, model=model, tools=list(by_name))
    pending: list[dict] | None = [{"role": "user", "content": question}]
    step = 0
    while pending is not None:
        if step == MAX_STEPS:
            run.reasons.append(f"Stopped at the {MAX_STEPS}-step limit without a checked answer.")
            break
        if clock() - started > TIME_BUDGET_S:
            run.reasons.append(f"Stopped at the {TIME_BUDGET_S:.0f}s time budget.")
            break
        step += 1
        pending = _step(responses, model, instructions, by_name, pending, run, step, trace)
    result = _result(question, run, step, clock() - started, trace.run_id)
    trace.event("end", steps=step, escalation=result.escalation, seconds=result.seconds)
    return result


def _result(question: str, run: _Run, steps: int, seconds: float, trace_id: str) -> AgentResult:
    answer, check = run.answer, run.check
    if answer is None:
        answer = FALLBACK_ANSWER
        check = verify_citations(answer, run.evidence)
    if run.reasons:
        # Never show a failed answer as if it passed: the flag travels with it.
        answer = answer.model_copy(update={"needs_human_review": True})
    return AgentResult(
        question=question,
        answer=answer,
        check=check,
        evidence=tuple(run.evidence),
        tool_calls=tuple(
            {"name": c.name, "arguments": c.arguments, "ok": c.ok, "seconds": round(c.seconds, 3)}
            for c in run.calls
        ),
        steps=steps,
        escalation=tuple(run.reasons),
        input_tokens=run.usage.input_tokens,
        output_tokens=run.usage.output_tokens,
        seconds=round(seconds, 2),
        trace_id=trace_id,
    )


def _call_line(call: Mapping[str, Any]) -> str:
    args = call["arguments"]
    shown = json.dumps(args, default=str) if isinstance(args, Mapping) else repr(args)
    return f"  {'ok ' if call['ok'] else 'ERR'} {call['name']}({shown})"


def render(result: AgentResult) -> str:
    a = result.answer
    banner = ["UNVERIFIED: failed the checks below; an engineer must review it.", ""]
    lines = [
        *(banner if result.escalation else []),
        with_disclaimer(a.answer),
        "",
        f"requirement: {a.requirement_type} | confidence: {a.confidence} | "
        f"departure: {a.is_departure} | human review: {result.needs_human_review}",
        f"cited: {', '.join(a.citations) or 'none'}",
        "tools:",
        *(_call_line(c) for c in result.tool_calls),
    ]
    lines += [f"escalation: {reason}" for reason in result.escalation]
    lines.append(
        f"{result.steps} step(s), {result.input_tokens} in / {result.output_tokens} out "
        f"tokens, {result.seconds}s, trace {result.trace_id}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wires live Azure clients
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from openai import OpenAIError

    from agent.rag import RagError
    from agent.tools.registry import build_tools
    from agent.trace import JsonlTrace
    from core.clients import chat_client, embedding_client
    from core.settings import SettingsError, load_settings
    from index.embed import embed_texts

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
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
        result = run_agent(
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
