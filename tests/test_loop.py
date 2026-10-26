"""Agent loop with a scripted fake model and fake tools: steps, checks, limits, escalation."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from agent.answer import Answer
from agent.loop import AGENT_RULES, MAX_STEPS, TIME_BUDGET_S, agent_instructions, render, run_agent
from agent.rag import DISCLAIMER, NO_ANSWER
from agent.tools.registry import Tool, ToolOutput
from tests.test_rag import GOOD, _answer


class _Args(BaseModel):
    query: str = ""


def _search(**kwargs) -> ToolOutput:
    return ToolOutput("<clause ...>", ("CD109-5.1", "CD109-Table-5.1"))


TOOLS = [Tool("search_standards", "Search the standards " * 3, _Args, _search)]


def _call(name: str = "search_standards", arguments: str = '{"query": "gradient"}', n: int = 1):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=f"c{n}")


def _blank_answer_error() -> ValidationError:
    try:
        _answer(" ", ())
    except ValidationError as err:
        return err
    raise AssertionError("blank answer should fail validation")


class FakeResponses:
    """Plays a script: a list of function calls, an Answer, None (no answer) or an exception."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        usage = SimpleNamespace(input_tokens=100, output_tokens=10)
        n = len(self.calls)
        if isinstance(step, list):
            return SimpleNamespace(id=f"r{n}", output=step, output_parsed=None, usage=usage)
        message = SimpleNamespace(type="message")
        return SimpleNamespace(id=f"r{n}", output=[message], output_parsed=step, usage=usage)


def _run(fake: FakeResponses, clock=None, tools=TOOLS):
    kwargs = {"clock": clock} if clock else {}
    return run_agent("Max motorway gradient?", tools, fake, "gpt-test", "Rules.", **kwargs)


def test_search_then_a_checked_answer():
    fake = FakeResponses([_call()], GOOD)
    result = _run(fake)

    assert result.answer == GOOD and result.escalation == () and result.check.ok
    assert result.steps == 2 and not result.needs_human_review
    assert result.evidence == ("CD109-5.1", "CD109-Table-5.1")
    assert result.tool_calls[0]["name"] == "search_standards" and result.tool_calls[0]["ok"]
    assert (result.input_tokens, result.output_tokens) == (200, 20)


def test_conversation_is_chained_and_tool_output_sent_back():
    fake = FakeResponses([_call(n=7)], GOOD)
    _run(fake)
    first, second = fake.calls
    assert "previous_response_id" not in first  # Azure rejects null
    assert second["previous_response_id"] == "r1"
    assert second["input"] == [
        {"type": "function_call_output", "call_id": "c7", "output": "<clause ...>"}
    ]
    assert first["text_format"] is Answer
    assert [t["name"] for t in first["tools"]] == ["search_standards"]
    assert first["instructions"] == agent_instructions("Rules.")


def test_citing_something_no_tool_returned_gets_one_retry():
    bad = _answer("Gradients [CD 109 cl. 9.9].", ("CD109-9.9",))
    fake = FakeResponses([_call()], bad, GOOD)
    result = _run(fake)
    assert result.answer == GOOD and result.escalation == ()
    assert "citation check" in fake.calls[2]["input"][0]["content"]


def test_two_failed_checks_escalate_with_the_last_answer():
    bad = _answer("Gradients [CD 109 cl. 9.9].", ("CD109-9.9",))
    result = _run(FakeResponses([_call()], bad, bad))
    assert result.answer.answer == bad.answer and result.answer.needs_human_review
    assert len(result.escalation) == 2 and "CD109-9.9" in result.escalation[0]


def test_answering_without_any_tool_fails_the_check():
    # Nothing was retrieved, so any citation is unverifiable.
    result = _run(FakeResponses(GOOD, GOOD))
    assert result.needs_human_review and result.escalation


def test_step_limit_escalates_with_the_fallback_answer():
    fake = FakeResponses(*[[_call(n=i)] for i in range(MAX_STEPS)])
    result = _run(fake)
    assert result.steps == MAX_STEPS and len(fake.calls) == MAX_STEPS
    assert result.answer.requirement_type == "not_covered" and result.needs_human_review
    assert "step limit" in result.escalation[-1]


def test_time_budget_stops_before_the_next_model_call():
    ticks = iter([0.0, 1.0, TIME_BUDGET_S + 1, TIME_BUDGET_S + 2, TIME_BUDGET_S + 3])
    fake = FakeResponses([_call()], GOOD)
    result = _run(fake, clock=lambda: next(ticks))
    assert len(fake.calls) == 1
    assert "time budget" in result.escalation[-1] and result.needs_human_review


def test_invalid_output_resends_the_tool_outputs_with_the_correction():
    fake = FakeResponses([_call()], _blank_answer_error(), GOOD)
    result = _run(fake)
    resent = fake.calls[2]
    assert resent["previous_response_id"] == "r1"  # the failed response never existed
    assert resent["input"][0]["type"] == "function_call_output"
    assert "not a valid answer" in resent["input"][1]["content"]
    assert result.answer == GOOD and result.escalation == ()


def test_no_answer_stops_and_escalates():
    result = _run(FakeResponses([_call()], None))
    assert result.escalation == (f"Step 2: {NO_ANSWER}",) and result.needs_human_review


def test_tool_errors_go_back_to_the_model():
    fake = FakeResponses([_call(arguments="{bad")], [_call(n=2)], GOOD)
    result = _run(fake)
    assert result.escalation == ()
    sent = fake.calls[1]["input"][0]["output"]
    assert "error" in json.loads(sent)
    assert not result.tool_calls[0]["ok"]


def test_empty_question_is_refused():
    with pytest.raises(ValueError):
        run_agent("  ", TOOLS, FakeResponses(), "m", "Rules.")


def test_events_are_traced_in_order():
    events = []

    class ListTrace:
        run_id = "t1"

        def event(self, kind, **data):
            events.append(kind)

    run_agent("q?", TOOLS, FakeResponses([_call()], GOOD), "m", "Rules.", trace=ListTrace())
    assert events == ["start", "model", "tool", "model", "check", "end"]


def test_render_adds_the_disclaimer_once_and_flags_unverified():
    clean = render(_run(FakeResponses([_call()], GOOD)))
    assert clean.count(DISCLAIMER) == 1 and "UNVERIFIED" not in clean
    assert 'ok  search_standards({"query": "gradient"})' in clean

    failed = render(_run(FakeResponses([_call()], None)))
    assert failed.startswith("UNVERIFIED")


def test_agent_rules_state_the_step_limit():
    assert str(MAX_STEPS) in agent_instructions("x")
    assert "{steps}" in AGENT_RULES


def test_tools_are_switched_off_on_the_last_step():
    fake = FakeResponses(*[[_call(n=i)] for i in range(MAX_STEPS - 1)], GOOD)
    result = _run(fake)
    assert fake.calls[-1]["tool_choice"] == "none"
    assert all("tool_choice" not in c for c in fake.calls[:-1])
    assert result.answer == GOOD and result.escalation == ()


def test_repeated_invalid_output_sends_only_the_latest_correction():
    error = _blank_answer_error()
    fake = FakeResponses([_call()], error, error, GOOD)
    _run(fake)
    third = fake.calls[3]["input"]
    assert [i.get("type", i.get("role")) for i in third] == ["function_call_output", "user"]


def test_a_failed_answer_then_no_answer_keeps_the_answer_and_both_reasons():
    bad = _answer("Gradients [CD 109 cl. 9.9].", ("CD109-9.9",))
    result = _run(FakeResponses([_call()], bad, None))
    assert result.answer.answer == bad.answer and result.answer.needs_human_review
    assert len(result.escalation) == 2


def test_the_answer_is_in_the_trace():
    events = []

    class ListTrace:
        run_id = "t1"

        def event(self, kind, **data):
            events.append((kind, data))

    run_agent("q?", TOOLS, FakeResponses([_call()], GOOD), "m", "Rules.", trace=ListTrace())
    check = dict(events)["check"]
    assert check["answer"]["answer"] == GOOD.answer
    assert dict(events)["tool"]["output"] == "<clause ...>"
