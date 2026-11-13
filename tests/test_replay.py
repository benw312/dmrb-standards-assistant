"""Replay mode: recorded answers served by the same engine interface, never improvised."""

import json
from datetime import date

import pytest
import streamlit as st
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from agent import engine as engine_module
from agent.citations import verify_citations
from agent.rag import RagResult
from agent.replay import (
    NotRecordedError,
    RecordedSet,
    Recording,
    ReplayAnswerer,
    ReplayError,
    load_recorded,
)
from app import api
from app.api import LazyAnswerer, create_app
from app.record_demo import MODES, record, write
from tests.test_api import FakeAnswerer, _agent_result
from tests.test_demo import DEMO
from tests.test_rag import GOOD

QUESTION = "Can I use a 5% gradient on a motorway?"


def _rag_result() -> RagResult:
    retrieved = ("CD109-5.1", "CD109-Table-5.1")
    return RagResult(
        question=QUESTION,
        answer=GOOD,
        retrieved=retrieved,
        check=verify_citations(GOOD, retrieved),
        attempts=1,
    )


def _recorded(**changes) -> RecordedSet:
    fields = {
        "recorded_on": date(2026, 11, 13),
        "model": "gpt-5.4-mini",
        "recordings": (
            Recording(question=QUESTION, mode="agent", result=_agent_result(question=QUESTION)),
            Recording(question=QUESTION, mode="rag", result=_rag_result()),
        ),
    }
    return RecordedSet(**(fields | changes))


@pytest.fixture
def recording(tmp_path, monkeypatch):
    """A recording on disk, with replay switched on."""
    path = tmp_path / "answers.json"
    write(_recorded(), path)
    monkeypatch.setattr("agent.replay.RECORDED_PATH", path)
    monkeypatch.setenv("DEMO_REPLAY", "1")
    return path


def test_a_recorded_question_replays_its_whole_result_per_pipeline():
    replay = ReplayAnswerer(_recorded())

    agent, rag = replay(QUESTION, "agent"), replay(QUESTION, "rag")

    assert agent == _agent_result(question=QUESTION) and isinstance(rag, RagResult)
    assert rag.check.ok and replay.questions == (QUESTION,)
    assert (replay.recorded_on, replay.model) == (date(2026, 11, 13), "gpt-5.4-mini")


def test_case_and_spacing_dont_matter_but_wording_does():
    replay = ReplayAnswerer(_recorded())
    assert replay("  can I use a 5%   GRADIENT on a motorway? ", "agent")
    with pytest.raises(NotRecordedError, match="only has answers for the example questions"):
        replay("Can I use a 6% gradient on a motorway?", "agent")  # never the nearest match
    with pytest.raises(NotRecordedError):
        replay(QUESTION, "graph")  # that pipeline wasn't recorded


def test_the_recording_round_trips_through_its_file(tmp_path):
    path = tmp_path / "demo" / "answers.json"
    write(_recorded(), path)
    assert load_recorded(path) == _recorded()
    # Agent and RAG results have different fields; each must come back as its own type.
    loaded = load_recorded(path).recordings
    assert [type(r.result).__name__ for r in loaded] == ["AgentResult", "RagResult"]


@pytest.mark.parametrize(
    "content", ["", "not json", "[]", '{"recorded_on": "2026-11-13"}', '{"recordings": 3}']
)
def test_a_bad_recording_file_is_one_clear_error(tmp_path, content):
    path = tmp_path / "answers.json"
    path.write_text(content)
    with pytest.raises(ReplayError, match="isn't a valid recording"):
        load_recorded(path)


def test_a_missing_recording_says_how_to_make_one(tmp_path):
    with pytest.raises(ReplayError, match="app.record_demo"):
        load_recorded(tmp_path / "answers.json")


def test_the_same_question_recorded_twice_is_refused():
    twice = _recorded().recordings * 2
    with pytest.raises(ReplayError, match="recorded twice"):
        ReplayAnswerer(_recorded(recordings=twice))


def test_recording_asks_every_question_in_every_pipeline_and_drops_local_trace_ids(capsys):
    answerer = FakeAnswerer(_agent_result())

    recorded = record(answerer, "gpt-5.4-mini", date(2026, 11, 13), questions=("Q1?", "Q2?"))

    assert answerer.calls == [(q, m) for q in ("Q1?", "Q2?") for m in MODES]
    assert all(r.result.trace_id == "" for r in recorded.recordings)
    assert len(recorded.recordings) == 6 and "Q2?" in capsys.readouterr().out


def test_a_failure_while_recording_writes_nothing(tmp_path):
    path = tmp_path / "answers.json"
    write(_recorded(), path)
    before = path.read_text()
    with pytest.raises(RuntimeError):
        record(FakeAnswerer(error=RuntimeError("down")), "m", date(2026, 11, 13))
    assert path.read_text() == before


def test_the_engine_switches_on_the_environment_variable(recording, monkeypatch):
    assert isinstance(engine_module.build_answerer(), ReplayAnswerer)
    live = FakeAnswerer(_agent_result())
    monkeypatch.setattr(engine_module, "build_live_answerer", lambda: live)
    for off in ("0", "", "true"):  # only "1" switches it on
        monkeypatch.setenv("DEMO_REPLAY", off)
        assert engine_module.build_answerer() is live


def test_the_api_replays_and_a_question_not_recorded_is_a_404(recording, monkeypatch):
    monkeypatch.setattr(api, "_LIVE", LazyAnswerer(engine_module.build_answerer))
    client = TestClient(create_app())

    ok = client.post("/ask", json={"question": QUESTION, "mode": "rag"})
    missing = client.post("/ask", json={"question": "What is the meaning of life?"})

    assert ok.status_code == 200 and ok.json()["citations"][0]["id"] == "CD109-5.1"
    assert missing.status_code == 404
    assert "recorded demo" in missing.json()["detail"] and missing.json()["request_id"]


def test_the_api_is_a_503_when_replay_is_on_but_nothing_is_recorded(recording, monkeypatch):
    recording.unlink()
    monkeypatch.setattr(api, "_LIVE", LazyAnswerer(engine_module.build_answerer))
    assert TestClient(create_app()).post("/ask", json={"question": QUESTION}).status_code == 503


@pytest.fixture
def demo():
    st.cache_resource.clear()
    yield lambda: AppTest.from_file(DEMO, default_timeout=30).run()
    st.cache_resource.clear()


def _ask(at: AppTest, question: str) -> AppTest:
    at.text_area(key="question").input(question)
    at.button[0].click()
    return at.run()


def test_the_demo_says_it_is_recorded_and_answers_a_recorded_question(recording, demo):
    at = demo()
    assert not at.exception
    notice = at.sidebar.info[0].value
    assert "Recorded demo" in notice and "13 Nov 2026" in notice and "gpt-5.4-mini" in notice

    at = _ask(at, QUESTION)

    assert not at.exception and "found in the text retrieved" in at.success[0].value


def test_the_demo_points_to_the_examples_for_a_question_not_recorded(recording, demo):
    at = _ask(demo(), "What is the meaning of life?")
    assert not at.exception and not at.error
    assert any("only has answers for the example questions" in i.value for i in at.info)


def test_the_demo_explains_a_missing_recording(recording, demo):
    recording.unlink()
    at = demo()
    assert not at.exception
    assert any("app.record_demo" in e.value for e in at.sidebar.error)


def test_live_mode_shows_no_recorded_notice(demo, monkeypatch):
    monkeypatch.delenv("DEMO_REPLAY", raising=False)
    monkeypatch.setattr(engine_module, "build_live_answerer", lambda: FakeAnswerer(_agent_result()))
    at = demo()
    assert not at.exception and len(at.sidebar.info) == 0


def test_the_file_is_plain_json_a_person_can_read(tmp_path):
    path = tmp_path / "answers.json"
    write(_recorded(), path)
    data = json.loads(path.read_text())
    assert data["recordings"][0]["result"]["answer"]["citations"] == [
        "CD109-5.1",
        "CD109-Table-5.1",
    ]


def test_the_committed_recording_answers_every_example_in_every_pipeline():
    # The demo offers these questions; in replay mode none of them may dead-end. Fails when
    # an example is added or reworded without re-running `python -m app.record_demo`.
    from app.presentation import EXAMPLES

    replay = ReplayAnswerer(load_recorded())
    for question in EXAMPLES:
        for mode in MODES:
            assert replay(question, mode).question == question


def test_changing_a_replayed_result_does_not_change_the_recording():
    replay = ReplayAnswerer(_recorded())
    replay(QUESTION, "agent").tool_calls[0]["name"] = "tampered"
    assert replay(QUESTION, "agent").tool_calls[0]["name"] == "search_standards"
