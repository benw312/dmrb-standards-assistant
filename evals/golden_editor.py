"""A form for writing the golden set (evals/golden.yaml), one question at a time.

    uv run streamlit run evals/golden_editor.py

It does the typing, not the thinking. You write the question, pick the clauses a correct
answer cites (a finder searches the indexed clauses on this machine), set the strength and
the departure flag, and say what a good answer says. The page checks the entry, shows the
YAML it will add, and appends it to the file.

Deliberately missing: anything that asks a model. No suggested answers, strengths or
departure flags: ground truth drafted by the system being measured would be worthless.
The only prefill is from your own retrieval questions (evals/retrieval.yaml).

The logic is in evals/golden_write.py; this file is the page.
"""

import re
import sys
from pathlib import Path

# `streamlit run` puts evals/ on the import path, not the repo root (as in app/demo.py).
ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import streamlit as st
from pydantic import ValidationError

from agent.citations import CitationError, display_citation, normalise_citation
from evals.golden import GoldenError, GoldenQuestion
from evals.golden_write import (
    TARGET,
    TYPES,
    append_question,
    counts,
    format_block,
    golden_path,
    load_or_empty,
    next_id,
    problems,
    search_chunks,
)
from evals.recall import QuestionsError, load_questions
from evals.yaml_io import first_error
from ingest.chunk import read_chunks
from ingest.models import Chunk

st.set_page_config(page_title="Golden set editor", page_icon="📝", layout="centered")

BLANK = "A blank question"
STRENGTHS = ("", "must", "shall", "should", "may", "note")
DEPARTURE = {"Not about a design": None, "Yes, a departure is needed": True, "No departure": False}
MESSAGES = {"success": st.success, "error": st.error}
TEXT_KEYS = ("question_text", "answer", "notes", "find")
FIELDS = "id, query, type, expected, requirement, departure, answer, notes"


@st.cache_resource(show_spinner="Reading the indexed clauses...")
def chunks() -> list[Chunk]:
    return read_chunks()


def _cite(chunk_id: str) -> str:
    return display_citation(chunk_id).strip("[]")


def _plain(text: str) -> str:
    """Text from the standard or the form, shown as text: '$' would start LaTeX."""
    return text.replace("$", r"\$")


def _label(text: str) -> str:
    """For widget labels, which render markdown: a heading's '*' or '_' stays a character."""
    return re.sub(r"([\\`*_\[\]$])", r"\\\1", text)


# --- callbacks: the one place Streamlit lets code set a widget's value -------------------


def _start_from() -> None:
    """Prefill from one of Ben's own retrieval questions, or clear the form."""
    state = st.session_state
    source = state.starters.get(state.start)
    state.question_text = source.query if source else ""
    state.expected = list(source.expected) if source else []
    state.notes = f"From retrieval question {source.id}." if source else ""
    state.answer = ""


def _add(chunk_id: str) -> None:
    if chunk_id not in st.session_state.expected:
        st.session_state.expected = [*st.session_state.expected, chunk_id]


def _remove(chunk_id: str) -> None:
    st.session_state.expected = [c for c in st.session_state.expected if c != chunk_id]


def _add_typed() -> None:
    """Without local chunks there is no finder, so a citation is typed and only its form checked."""
    state = st.session_state
    try:
        _add(normalise_citation(state.find))
    except CitationError as err:
        state.message = ("error", str(err))
    else:
        state.find = ""


def _entry() -> dict:
    """The form as a golden.yaml row. Fields that don't apply are left out, as in the file."""
    state = st.session_state
    row: dict = {"id": state.new_id.strip(), "query": state.question_text, "type": state.kind}
    if state.kind != "out_of_scope":
        row["expected"] = list(state.expected)
        if state.requirement:
            row["requirement"] = state.requirement
        if DEPARTURE[state.departure] is not None:
            row["departure"] = DEPARTURE[state.departure]
    if state.answer.strip():
        row["answer"] = state.answer.strip()
    if state.notes.strip():
        row["notes"] = state.notes.strip()
    return row


def _checked(existing: list[GoldenQuestion], known: set[str] | None):
    """(question, problems): the question if the form is a valid entry, and what's wrong."""
    try:
        question = GoldenQuestion.model_validate(_entry())
    except ValidationError as err:
        return None, [first_error(err, FIELDS).removeprefix("entry: ")]
    return question, problems(question, existing, known)


def _save(known: set[str] | None) -> None:
    state = st.session_state
    path = golden_path()
    try:
        existing = load_or_empty(path)
        question, wrong = _checked(existing, known)
        if wrong:
            state.message = ("error", "Not saved: " + "; ".join(wrong))
            return
        append_question(path, question)
    except GoldenError as err:
        state.message = ("error", f"Not saved: {err}")
        return
    state.message = ("success", f"Saved {question.id} to {path.name}.")
    state.question_text, state.answer, state.notes, state.find = "", "", "", ""
    state.expected, state.requirement, state.start = [], "", BLANK
    state.departure = next(iter(DEPARTURE))
    state.new_id = next_id([*existing, question])


# --- page -------------------------------------------------------------------------------


def _progress(existing: list[GoldenQuestion]) -> None:
    low, high = TARGET
    st.progress(
        min(len(existing) / low, 1.0),
        text=f"{len(existing)} question{'' if len(existing) == 1 else 's'} (target {low}–{high})",
    )
    tally = counts(existing)
    st.caption(" · ".join(f"{kind.replace('_', ' ')}: {n}" for kind, n in tally.items()))
    if empty := [kind.replace("_", " ") for kind, n in tally.items() if n == 0]:
        st.caption(f"None yet: {', '.join(empty)}.")
    if existing:
        with st.expander("Questions so far"):
            for q in existing:
                st.markdown(f"`{q.id}` {q.type.replace('_', ' ')}: {_plain(q.query)}")


def _finder(all_chunks: list[Chunk]) -> None:
    st.text_input(
        "Find a clause: a citation (CD 109 cl. 5.1) or words (gradient motorway)", key="find"
    )
    if not all_chunks:
        st.button("Add this citation", on_click=_add_typed)
        return
    hits = search_chunks(all_chunks, st.session_state.find)
    if st.session_state.find.strip() and not hits:
        st.caption("Nothing found. Every word must appear in the clause; try fewer words.")
    for hit in hits:
        strength = (hit.requirement_type or hit.kind).upper()
        heading = _label(hit.heading_path)
        title = f"{_cite(hit.citation_id)} · {strength} · {heading} (p. {hit.page})"
        with st.expander(title):
            st.text(hit.text)
            st.button(
                "Cite this",
                key=f"add_{hit.citation_id}",
                on_click=_add,
                args=(hit.citation_id,),
                disabled=hit.citation_id in st.session_state.expected,
            )


def _chosen() -> None:
    if not st.session_state.expected:
        st.caption("No clauses chosen yet. A correct answer must cite at least one.")
        return
    st.markdown("**A correct answer cites:**")
    for chunk_id in st.session_state.expected:
        name, remove = st.columns([5, 1])
        name.markdown(f"`{_cite(chunk_id)}`")
        remove.button("Remove", key=f"remove_{chunk_id}", on_click=_remove, args=(chunk_id,))


def _starters(existing: list[GoldenQuestion]) -> dict:
    """Ben's retrieval questions not yet in the golden set, by menu label."""
    try:
        retrieval = load_questions()
    except QuestionsError:
        return {}  # optional: the form works without them
    asked = {q.query.casefold() for q in existing}
    return {f"{r.id}: {r.query}": r for r in retrieval if r.query.casefold() not in asked}


def main() -> None:
    state = st.session_state
    path = golden_path()
    st.title("Golden set editor")
    st.write(
        "Write the ground truth for the answer evaluation, one question at a time. Nothing "
        "here asks a model: the question, clauses, strength and answer are yours."
    )
    try:
        existing = load_or_empty(path)
    except GoldenError as err:
        st.error(f"Fix this in {path.name} first; nothing can be added until it loads.")
        st.code(str(err), language=None)
        return
    try:
        all_chunks = chunks()
    except ValueError as err:
        st.error(f"data/chunks/ can't be read: {err}")
        return
    known = {c.citation_id for c in all_chunks} or None
    if known is None:
        st.warning(
            "No clauses found in data/chunks/ (run `uv run python -m ingest.chunk`). You can "
            "still add questions; citations are checked for form only, not that they exist."
        )

    state.setdefault("expected", [])
    state.setdefault("new_id", next_id(existing))
    for key in TEXT_KEYS:
        state.setdefault(key, "")
    state.starters = _starters(existing)

    _progress(existing)
    if message := state.pop("message", None):
        MESSAGES[message[0]](_plain(message[1]))
    st.caption(f"Saving to `{path}`")

    st.subheader("1. The question")
    st.selectbox(
        "Start from",
        [BLANK, *state.starters],
        key="start",
        on_change=_start_from,
        help="Your retrieval questions that aren't in the golden set yet.",
    )
    st.text_area("Question, as an engineer would ask it", key="question_text", height=80)
    st.radio(
        "Type",
        list(TYPES),
        key="kind",
        format_func=lambda kind: f"{kind.replace('_', ' ')}: {TYPES[kind]}",
    )

    if state.kind != "out_of_scope":
        st.subheader("2. The clauses a correct answer cites")
        st.caption("All of them: scoring measures how many of these an answer finds.")
        _finder(all_chunks)
        _chosen()
        st.subheader("3. Strength and departure")
        st.selectbox(
            "Strength of the governing clause (optional)",
            STRENGTHS,
            key="requirement",
            format_func=lambda verb: verb or "(leave out)",
        )
        st.radio(
            "Would the design described need a Departure from Standard?",
            list(DEPARTURE),
            key="departure",
            help="Required for a departure question.",
        )
    else:
        st.info("An out-of-scope question has no clauses, strength or departure flag.")

    st.subheader(
        "4. What a good answer says"
        if state.kind != "out_of_scope"
        else "2. What a good answer says"
    )
    st.text_area(
        "In a sentence or two (used by the AI judges, and by you)", key="answer", height=80
    )
    st.text_input("Notes (optional)", key="notes")
    st.text_input("ID", key="new_id", max_chars=20)

    question, wrong = _checked(existing, known)
    if question is not None:
        st.caption("This is what will be added to the file:")
        st.code(format_block(question), language="yaml")
    for problem in wrong if state.question_text.strip() else []:
        st.caption(f"⚠ {_plain(problem)}")
    st.button("Save question", type="primary", on_click=_save, args=(known,))


main()
