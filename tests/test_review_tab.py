"""The demo's Review queue tab, headless: a flagged answer is queued, judged and exported."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from agent import engine as engine_module
from app.review_tab import split_citations
from review.queue import ReviewQueue, Verdict
from tests.test_api import FakeAnswerer, _agent_result
from tests.test_demo import DEMO
from tests.test_review_queue import flagged

ESCALATED = ("Answer 2: citation check failed: CD109-5.9 was not retrieved.",)


@pytest.fixture
def demo(monkeypatch):
    def run(result=None) -> AppTest:
        answerer = FakeAnswerer(result or _agent_result())
        monkeypatch.setattr(engine_module, "build_live_answerer", lambda: answerer)
        st.cache_resource.clear()
        return AppTest.from_file(DEMO, default_timeout=30).run()

    yield run
    st.cache_resource.clear()


def _submit(at: AppTest, reviewer="Ben Wallbridge", outcome="approved", note="", expected=""):
    at.text_input(key="reviewer").input(reviewer)
    at.radio(key="outcome").set_value(outcome)
    at.text_area(key="note").input(note)
    at.text_input(key="expected").input(expected)
    next(b for b in at.button if b.label == "Record verdict").click()
    return at.run()


def _texts(at: AppTest) -> str:
    return "\n".join(e.value for e in (*at.markdown, *at.caption, *at.info, *at.success))


def test_an_empty_queue_says_so(demo):
    at = demo()
    assert not at.exception
    assert "Nothing is waiting for review." in _texts(at)
    assert "0 waiting · 0 approved · 0 corrected" in _texts(at)


def test_asking_a_question_that_escalates_puts_it_on_the_queue(demo, review_queue):
    at = demo(_agent_result(escalation=ESCALATED))
    at.text_area(key="question").input("Max motorway gradient?")
    at.button[0].click()
    at.run()

    assert not at.exception
    (item,) = review_queue.list("pending")
    assert f"On the review queue as #{item.id}" in _texts(at)
    assert "1 waiting" in _texts(at) and "citation check failed" in _texts(at)


def test_a_clean_answer_is_not_queued(demo, review_queue):
    at = demo()
    at.text_area(key="question").input("Max motorway gradient?")
    at.button[0].click()
    at.run()
    assert review_queue.list() == [] and "On the review queue" not in _texts(at)


def test_the_tab_shows_what_the_engineer_needs_to_judge(demo, review_queue):
    review_queue.add(flagged())
    text = _texts(demo())
    assert "Can I use a 5% gradient on a motorway?" in text
    assert "possible Departure from Standard" in text
    assert "It cited: CD 109 cl. 5.1" in text and "departure needed: yes" in text
    assert "traces/20261113-000000-abc123.jsonl" in text


def test_approving_resolves_the_item_and_moves_to_the_next(demo, review_queue):
    first = review_queue.add(flagged(question="First question?"))
    review_queue.add(flagged(question="Second question?"))

    at = _submit(demo())

    assert not at.exception
    done = review_queue.get(first.id)
    assert done.verdict == Verdict(outcome="approved", reviewer="Ben Wallbridge")
    assert f"Recorded: #{first.id} approved by Ben Wallbridge." in _texts(at)
    assert "1 waiting · 1 approved" in _texts(at) and "Second question?" in _texts(at)
    assert at.text_input(key="reviewer").value == "Ben Wallbridge"  # kept for the next one


def test_a_correction_records_the_note_and_clauses_and_shows_as_a_candidate(demo, review_queue):
    item = review_queue.add(flagged())
    review_queue.add(flagged(question="Next question?"))  # so the form is shown again

    at = _submit(
        demo(),
        outcome="corrected",
        note="4% is the limit (shall).",
        expected="CD 109 cl. 5.1; CD 109 Table 5.1",
    )

    verdict = review_queue.get(item.id).verdict
    assert verdict.note == "4% is the limit (shall)."
    assert verdict.expected == ("CD109-5.1", "CD109-Table-5.1")
    assert at.text_area(key="note").value == ""  # cleared for the next review
    (yaml_block,) = at.code
    assert "- id: R001" in yaml_block.value
    assert "expected: [CD 109 cl. 5.1, CD 109 Table 5.1]" in yaml_block.value


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"outcome": "corrected"}, "needs a note"),
        ({"reviewer": ""}, "reviewer"),
        ({"expected": "somewhere in section 5"}, "expected"),
    ],
)
def test_a_bad_verdict_is_explained_and_nothing_is_recorded(demo, review_queue, fields, message):
    item = review_queue.add(flagged())
    at = _submit(demo(), **fields)
    assert not at.exception
    assert any("Not recorded" in e.value and message in e.value for e in at.error)
    assert review_queue.get(item.id).status == "pending"


def test_a_verdict_given_elsewhere_meanwhile_is_left_alone(demo, review_queue):
    item = review_queue.add(flagged())
    at = demo()
    review_queue.resolve(item.id, Verdict(outcome="corrected", reviewer="Other", note="No."))

    at = _submit(at)

    assert not at.exception
    assert review_queue.get(item.id).verdict.reviewer == "Other"


def test_an_unreadable_queue_is_an_error_not_a_crash(demo, monkeypatch, tmp_path):
    from app import review_api

    broken = tmp_path / "broken.sqlite"
    broken.write_text("not a database " * 30)
    monkeypatch.setattr(review_api, "_QUEUE", ReviewQueue(broken))
    at = demo()
    assert not at.exception
    assert any("review queue can't be read" in e.value for e in at.error)


def test_model_text_in_the_queue_is_rendered_safely(demo, review_queue):
    review_queue.add(flagged(answer="See ![x](https://evil.example/p.png) costs $5"))
    text = _texts(demo())
    assert "evil.example" not in text and "[image removed]" in text


def test_split_citations_accepts_commas_semicolons_and_lines():
    assert split_citations(" CD 109 cl. 5.1, CD 109 Table 5.1;\nCG 501 cl. 7.5.4 ") == [
        "CD 109 cl. 5.1",
        "CD 109 Table 5.1",
        "CG 501 cl. 7.5.4",
    ]
    assert split_citations("  ") == []
