"""The Streamlit page itself, run headless by AppTest with a fake engine. No Azure."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from agent import engine as engine_module
from core.settings import SettingsError
from tests.test_api import FakeAnswerer, _agent_result

DEMO = str(Path(__file__).resolve().parent.parent / "app" / "demo.py")


@pytest.fixture
def run_demo(monkeypatch):
    def run(answerer) -> AppTest:
        monkeypatch.setattr(engine_module, "build_live_answerer", lambda: answerer)
        st.cache_resource.clear()  # the engine is cached across runs
        at = AppTest.from_file(DEMO, default_timeout=30)
        return at.run()

    yield run
    st.cache_resource.clear()


def _ask(at: AppTest, question: str) -> AppTest:
    at.text_area(key="question").input(question)
    at.button[0].click()
    return at.run()


def test_an_empty_question_asks_for_one(run_demo):
    answerer = FakeAnswerer(_agent_result())
    at = run_demo(answerer)
    at.button[0].click()
    at.run()
    assert [w.value for w in at.warning] == ["Type a question first."]
    assert answerer.calls == []


def test_a_clean_answer_shows_flags_and_citations(run_demo):
    at = _ask(run_demo(FakeAnswerer(_agent_result())), "Max motorway gradient?")
    assert not at.exception
    assert "found in the text retrieved" in at.success[0].value
    assert [m.label for m in at.metric] == ["Governing clause", "Departure needed?"]
    assert any("`CD 109 cl. 5.1`" in m.value for m in at.markdown)


def test_an_escalated_answer_hides_its_flags(run_demo):
    result = _agent_result(escalation=("Answer 2: citation check failed: X",))
    at = _ask(run_demo(FakeAnswerer(result)), "q?")
    assert "Unverified" in at.error[0].value
    assert len(at.metric) == 0  # no "Departure needed? No" on an unchecked answer


def test_the_answer_survives_changing_a_setting(run_demo):
    at = _ask(run_demo(FakeAnswerer(_agent_result())), "Max motorway gradient?")
    at.sidebar.radio[0].set_value("Agent on LangGraph").run()
    assert at.success  # still shown, labelled with the pipeline that produced it
    assert any("pipeline: agent" in c.value for c in at.caption)


def test_picking_an_example_fills_the_question(run_demo):
    at = run_demo(FakeAnswerer(_agent_result()))
    at.selectbox(key="example").set_value("Can I use a 5% gradient on a motorway?").run()
    assert at.text_area(key="question").value == "Can I use a 5% gradient on a motorway?"


def test_engine_failures_are_shown_by_kind(run_demo):
    at = _ask(run_demo(FakeAnswerer(error=SettingsError("AZURE_SEARCH_ENDPOINT missing"))), "q?")
    assert "isn't configured" in at.error[0].value
    at = _ask(run_demo(FakeAnswerer(error=KeyError("x"))), "q?")
    assert "Something went wrong (KeyError)" in at.error[0].value
