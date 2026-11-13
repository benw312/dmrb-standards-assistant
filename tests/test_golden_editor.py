"""The golden set editor page, headless, on a temp golden file and fake clauses."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from evals.golden import load_golden
from evals.golden_write import append_question, load_or_empty
from evals.recall import QuestionsError, RetrievalQuestion
from tests.test_golden_write import CHUNKS, OUT_OF_SCOPE, question

EDITOR = str(Path(__file__).resolve().parent.parent / "evals" / "golden_editor.py")
RETRIEVAL = [
    RetrievalQuestion(id="R01", query="Max motorway gradient?", expected=["CD 109 cl. 5.1"]),
    RetrievalQuestion(id="R02", query="What superelevation?", expected=["CD 109 cl. 4.2"]),
]


@pytest.fixture
def editor(monkeypatch):
    def run(chunks=CHUNKS, retrieval=RETRIEVAL) -> AppTest:
        def load_questions():
            if retrieval is None:
                raise QuestionsError("retrieval.yaml not found")
            return retrieval

        monkeypatch.setattr("ingest.chunk.read_chunks", lambda: list(chunks))
        monkeypatch.setattr("evals.recall.load_questions", load_questions)
        st.cache_resource.clear()
        return AppTest.from_file(EDITOR, default_timeout=30).run()

    yield run
    st.cache_resource.clear()


def _button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def _text(at: AppTest) -> str:
    kinds = (at.markdown, at.caption, at.info, at.success, at.error, at.warning)
    return "\n".join(e.value for kind in kinds for e in kind)


def _fill(at: AppTest, query="Can I use a 5% gradient on a motorway?", answer="No: 4% (shall)."):
    at.text_area(key="question_text").input(query)
    at.text_area(key="answer").input(answer)
    return at.run()


def test_an_empty_set_shows_the_target_and_what_is_missing(editor):
    at = editor()
    assert not at.exception
    assert "lookup: 0" in _text(at) and "None yet: lookup, multi hop" in _text(at)
    assert at.text_input(key="new_id").value == "G01"


def test_a_whole_question_is_saved_and_the_form_resets(editor, golden_file):
    at = editor()
    at.radio(key="kind").set_value("departure")
    at = _fill(at)
    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    at.button(key="add_CD109-5.1").click().run()
    at.text_input(key="find").input("motorway gradients").run()
    at.button(key="add_CD109-Table-5.1").click().run()
    at.selectbox(key="requirement").set_value("shall")
    at.radio(key="departure").set_value("Yes, a departure is needed").run()

    assert "expected: [CD 109 cl. 5.1, CD 109 Table 5.1]" in at.code[0].value  # the preview
    _button(at, "Save question").click().run()

    assert not at.exception and "Saved G01 to golden.yaml." in _text(at)
    (saved,) = load_golden(golden_file)
    assert saved == question(answer="No: 4% (shall).")
    assert at.text_area(key="question_text").value == "" and at.text_area(key="answer").value == ""
    assert at.text_input(key="new_id").value == "G02"
    assert "departure: 1" in _text(at) and "No clauses chosen yet" in _text(at)


def test_an_out_of_scope_question_needs_no_clauses(editor, golden_file):
    at = editor()
    at.radio(key="kind").set_value("out_of_scope").run()
    assert "has no clauses, strength or departure flag" in _text(at)
    assert not [w for w in at.text_input if w.key == "find"]

    at = _fill(at, OUT_OF_SCOPE.query, OUT_OF_SCOPE.answer)
    _button(at, "Save question").click().run()

    assert load_golden(golden_file) == [OUT_OF_SCOPE.model_copy(update={"id": "G01"})]


def test_clauses_chosen_earlier_are_not_saved_on_an_out_of_scope_question(editor, golden_file):
    at = editor()
    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    at.button(key="add_CD109-5.1").click().run()
    at.radio(key="kind").set_value("out_of_scope").run()
    at = _fill(at, OUT_OF_SCOPE.query, OUT_OF_SCOPE.answer)
    _button(at, "Save question").click().run()
    assert load_golden(golden_file)[0].expected == ()


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ({}, "expected: list at least one clause"),
        ({"kind": "departure", "cite": True}, "a departure question needs departure"),
        ({"cite": True, "answer": ""}, "answer: say what a good answer says"),
        ({"cite": True, "new_id": "EXAMPLE-1"}, "template's example"),
        ({"cite": True, "new_id": ""}, "id"),
    ],
)
def test_an_incomplete_question_is_explained_and_not_saved(editor, golden_file, setup, message):
    at = editor()
    at.radio(key="kind").set_value(setup.get("kind", "lookup"))
    at = _fill(at, answer=setup.get("answer", "No."))
    if setup.get("cite"):
        at.text_input(key="find").input("CD 109 cl. 5.1").run()
        at.button(key="add_CD109-5.1").click().run()
    if "new_id" in setup:
        at.text_input(key="new_id").input(setup["new_id"]).run()

    assert message in _text(at)  # shown before saving
    _button(at, "Save question").click().run()

    assert not at.exception and any("Not saved" in e.value and message in e.value for e in at.error)
    assert load_or_empty(golden_file) == []
    assert at.text_area(key="question_text").value  # what was typed is kept


def test_a_question_already_in_the_set_is_refused(editor, golden_file):
    append_question(golden_file, question())
    at = editor()
    at = _fill(at)
    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    at.button(key="add_CD109-5.1").click().run()
    _button(at, "Save question").click().run()
    assert any("G01 already asks this question" in e.value for e in at.error)
    assert load_golden(golden_file) == [question()]
    assert at.text_input(key="new_id").value == "G02"


def test_a_clause_can_be_removed_and_cited_once_only(editor):
    at = editor()
    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    at.button(key="add_CD109-5.1").click().run()
    assert at.button(key="add_CD109-5.1").disabled and "`CD 109 cl. 5.1`" in _text(at)
    at.button(key="remove_CD109-5.1").click().run()
    assert "No clauses chosen yet" in _text(at) and not at.button(key="add_CD109-5.1").disabled


def test_the_finder_shows_strength_heading_page_and_the_clause_text(editor):
    at = editor()
    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    labels = [e.label for e in at.expander]
    assert "CD 109 cl. 5.1 · SHALL · 5. Vertical alignment > Gradients (p. 25)" in labels
    assert any("shall not exceed Table 5.1" in t.value for t in at.text)
    at.text_input(key="find").input("unicorn").run()
    assert "Nothing found" in _text(at)


def test_starting_from_a_retrieval_question_prefills_it_and_used_ones_drop_out(editor, golden_file):
    at = editor()
    at.selectbox(key="start").set_value("R01: Max motorway gradient?").run()
    assert at.text_area(key="question_text").value == "Max motorway gradient?"
    assert "`CD 109 cl. 5.1`" in _text(at)
    assert at.text_input(key="notes").value == "From retrieval question R01."

    at.text_area(key="answer").input("4% (shall).").run()
    _button(at, "Save question").click().run()

    assert load_golden(golden_file)[0].expected == ("CD109-5.1",)
    assert at.selectbox(key="start").options == ["A blank question", "R02: What superelevation?"]


def test_going_back_to_blank_clears_the_prefill(editor):
    at = editor()
    at.selectbox(key="start").set_value("R01: Max motorway gradient?").run()
    at.selectbox(key="start").set_value("A blank question").run()
    assert at.text_area(key="question_text").value == "" and "No clauses chosen yet" in _text(at)


def test_it_works_without_retrieval_questions(editor):
    at = editor(retrieval=None)
    assert not at.exception and at.selectbox(key="start").options == ["A blank question"]


def test_without_local_clauses_a_citation_is_typed_and_checked_for_form(editor, golden_file):
    at = editor(chunks=[])
    assert "No clauses found in data/chunks/" in _text(at)
    at.text_input(key="find").input("somewhere in section 5").run()
    _button(at, "Add this citation").click().run()
    assert at.error and "No clauses chosen yet" in _text(at)

    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    _button(at, "Add this citation").click().run()
    at = _fill(at)
    _button(at, "Save question").click().run()
    assert load_golden(golden_file)[0].expected == ("CD109-5.1",)


def test_a_clause_that_isnt_indexed_is_refused_when_clauses_are_local(editor, golden_file):
    at = editor()
    at.selectbox(key="start").set_value("R02: What superelevation?").run()  # cites cl. 4.2
    at.text_area(key="answer").input("6.93%.").run()
    assert "Saved" not in _text(at)
    at = editor(chunks=CHUNKS[:1])  # an index without cl. 4.2
    at.selectbox(key="start").set_value("R02: What superelevation?").run()
    at.text_area(key="answer").input("6.93%.").run()
    _button(at, "Save question").click().run()
    assert any("not in the indexed standards: CD 109 cl. 4.2" in e.value for e in at.error)
    assert load_or_empty(golden_file) == []


def test_a_file_that_doesnt_load_blocks_the_form_and_says_why(editor, golden_file):
    golden_file.write_text('- id: G01\n  query: "Q?"\n  type: nonsense\n')
    at = editor()
    assert not at.exception
    assert any("Fix this in golden.yaml first" in e.value for e in at.error)
    assert "type" in at.code[0].value and not [b for b in at.button if b.label == "Save question"]


def test_questions_so_far_are_listed(editor, golden_file):
    append_question(golden_file, question())
    append_question(golden_file, OUT_OF_SCOPE)
    text = _text(editor())
    assert "`G01` departure: Can I use a 5% gradient on a motorway?" in text
    assert "`G02` out of scope" in text


def test_markdown_characters_in_a_heading_stay_characters(editor):
    odd = CHUNKS[0].model_copy(update={"heading_path": "5. Costs in $ > *Gradients* [draft]_x_"})
    at = editor(chunks=[odd])
    at.text_input(key="find").input("CD 109 cl. 5.1").run()
    assert r"5. Costs in \$ > \*Gradients\* \[draft\]\_x\_ (p. 25)" in at.expander[0].label
