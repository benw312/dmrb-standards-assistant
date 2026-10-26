"""LangGraph agent: same behaviour as agent/loop.py on the same scripts, plus pause for review."""

import warnings

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph, initial_state, run_graph, to_result
from agent.loop import MAX_STEPS, TIME_BUDGET_S, run_agent
from tests.test_loop import TOOLS, FakeResponses, _answer, _blank_answer_error, _call
from tests.test_rag import GOOD

BAD = _answer("Gradients [CD 109 cl. 9.9].", ("CD109-9.9",))

SCRIPTS = {
    "search then answer": lambda: ([_call()], GOOD),
    "retry passes": lambda: ([_call()], BAD, GOOD),
    "two failed checks": lambda: ([_call()], BAD, BAD),
    "no answer": lambda: ([_call()], None),
    "invalid output then answer": lambda: ([_call()], _blank_answer_error(), GOOD),
    "invalid output on the last step": lambda: (
        *([_call(n=i)] for i in range(MAX_STEPS - 1)),
        _blank_answer_error(),
    ),
    "step limit": lambda: tuple([_call(n=i)] for i in range(MAX_STEPS)),
    "answers on the last step": lambda: (*([_call(n=i)] for i in range(MAX_STEPS - 1)), GOOD),
    "bad tool call": lambda: ([_call(arguments="{bad")], [_call(n=2)], GOOD),
}


class ListTrace:
    run_id = "t1"

    def __init__(self):
        self.events = []

    def event(self, kind, **data):
        self.events.append((kind, sorted(k for k in data if k != "runner")))


def _same(result):
    """The whole result, minus wall-clock times."""
    dumped = result.model_dump()
    dumped["seconds"] = None
    for call in dumped["tool_calls"]:
        call["seconds"] = None
    return dumped


def _both(script, clock=None):
    kwargs = {"clock": clock} if clock else {}
    runs = []
    for runner in (run_agent, run_graph):
        fake, trace = FakeResponses(*script()), ListTrace()
        result = runner("q?", TOOLS, fake, "m", "Rules.", trace=trace, **kwargs)
        runs.append((result, fake.calls, trace.events))
    return runs


@pytest.mark.parametrize("name", SCRIPTS)
def test_graph_matches_the_loop(name):
    (loop, loop_calls, loop_events), (graph, graph_calls, graph_events) = _both(SCRIPTS[name])
    assert _same(graph) == _same(loop)
    assert graph_calls == loop_calls  # identical requests, incl. tool_choice on the last step
    assert graph_events == loop_events  # same trace events with the same fields


def test_an_expired_budget_stops_both_before_the_first_call():
    def clock_factory():
        ticks = iter([0.0, *[TIME_BUDGET_S + 1] * 10])
        return lambda: next(ticks)

    loop_fake, graph_fake = FakeResponses([_call()], GOOD), FakeResponses([_call()], GOOD)
    loop = run_agent("q?", TOOLS, loop_fake, "m", "Rules.", clock=clock_factory())
    graph = run_graph("q?", TOOLS, graph_fake, "m", "Rules.", clock=clock_factory())
    assert loop_fake.calls == graph_fake.calls == []
    assert "time budget" in graph.escalation[-1] and _same(graph) == _same(loop)


def test_time_budget_after_the_first_call():
    fake = FakeResponses([_call()], GOOD)
    clock = lambda: TIME_BUDGET_S + 1 if fake.calls else 0.0  # noqa: E731
    result = run_graph("q?", TOOLS, fake, "m", "Rules.", clock=clock)
    assert len(fake.calls) == 1 and "time budget" in result.escalation[-1]


def _paused(script, thread="q1"):
    app = build_graph(
        TOOLS,
        FakeResponses(*script),
        "m",
        "Rules.",
        clock=lambda: 0.0,
        checkpointer=InMemorySaver(),
        pause_for_review=True,
    )
    config = {"configurable": {"thread_id": thread}}
    app.invoke(initial_state("q?", 0.0), config)
    return app, config


@pytest.mark.parametrize(
    "make_script",
    [
        lambda: ([_call()], BAD, BAD),
        lambda: ([_call()], None),
        lambda: tuple([_call(n=i)] for i in range(MAX_STEPS)),
    ],
    ids=["failed checks", "no answer", "step limit"],
)
def test_every_escalation_pauses_before_review_and_resumes(make_script):
    with warnings.catch_warnings():
        # Fails if checkpoint state holds a type the serialiser will soon refuse.
        warnings.simplefilter("error")
        app, config = _paused(make_script())
        assert app.get_state(config).next == ("review",)  # waiting for an engineer
        final = app.invoke(None, config)  # the engineer resumes

    assert app.get_state(config).next == ()
    resumed = to_result(final, 0.0, "t")
    assert resumed.needs_human_review and resumed.escalation
    assert isinstance(resumed.tool_calls, tuple)  # validated, not a list from the checkpoint
    loop = run_agent("q?", TOOLS, FakeResponses(*make_script()), "m", "Rules.", clock=lambda: 0.0)
    assert _same(resumed) | {"trace_id": None} == _same(loop) | {"trace_id": None}


def test_a_clean_run_never_pauses():
    app, config = _paused(([_call()], GOOD), thread="q2")
    assert app.get_state(config).next == ()


def test_pausing_needs_a_checkpointer():
    with pytest.raises(ValueError, match="checkpointer"):
        build_graph(TOOLS, FakeResponses(), "m", "Rules.", pause_for_review=True)


def test_empty_question_is_refused():
    with pytest.raises(ValueError):
        run_graph(" ", TOOLS, FakeResponses(), "m", "Rules.")


def test_mermaid_diagram_names_every_node():
    diagram = build_graph([], FakeResponses(), "m", "Rules.").get_graph().draw_mermaid()
    for node in ("model", "tools", "check", "limit", "review"):
        assert f"{node}({node})" in diagram
