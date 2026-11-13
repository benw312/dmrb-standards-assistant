"""The demo's "Review queue" tab: an engineer works through flagged answers, one verdict each.

Rendering only: storage and rules are in review/queue.py, and the same queue is served over
REST by app/review_api.py. Each verdict then shows as a golden-set candidate
(review/export.py), which is how a review becomes a test case.
"""

import re

import streamlit as st
from pydantic import ValidationError

from agent.citations import CitationError, display_citation
from app.presentation import STRENGTH_LABELS, safe_markdown
from review.export import candidates
from review.queue import (
    AlreadyResolvedError,
    Outcome,
    ReviewError,
    ReviewItem,
    ReviewQueue,
    Verdict,
)

OUTCOMES: dict[Outcome, str] = {
    "approved": "Approved: the answer is right as given",
    "corrected": "Corrected: the answer is wrong (say what's right in the note)",
}
RESOLVED_SHOWN = 20
MESSAGES = {"success": st.success, "warning": st.warning, "error": st.error}
_SEPARATORS = re.compile(r"[,;\n]")


def split_citations(text: str) -> list[str]:
    """'CD 109 cl. 5.1, CD 109 Table 5.1' -> one entry per clause."""
    return [part.strip() for part in _SEPARATORS.split(text) if part.strip()]


def _cite(chunk_id: str) -> str:
    try:
        return display_citation(chunk_id).strip("[]")
    except CitationError:
        return chunk_id


def _label(item: ReviewItem) -> str:
    question = item.flagged.question
    return f"#{item.id}  {question[:80]}{'…' if len(question) > 80 else ''}"


def _show_flagged(item: ReviewItem) -> None:
    flagged = item.flagged
    st.markdown(f"**Question:** {safe_markdown(flagged.question)}")
    st.caption(
        f"Asked {item.created_at:%Y-%m-%d %H:%M} UTC · pipeline: {flagged.mode} · "
        f"confidence: {flagged.confidence}"
    )
    st.markdown("**Flagged because:**")
    for reason in flagged.reasons:
        st.markdown(f"- {safe_markdown(reason)}")
    st.markdown("**The model's answer** (awaiting your verdict):")
    with st.container(border=True):
        st.markdown(safe_markdown(flagged.answer))
    departure = {True: "yes", False: "no", None: "n/a"}[flagged.is_departure]
    st.caption(
        f"It said: {STRENGTH_LABELS[flagged.requirement_type]} · departure needed: {departure}"
    )
    if flagged.citations:
        st.caption("It cited: " + ", ".join(_cite(c) for c in flagged.citations))
    if flagged.trace_id:
        st.caption(f"Trace: traces/{flagged.trace_id}.jsonl")


def _record(queue: ReviewQueue, review_id: int) -> None:
    """The submit button's callback. A callback runs before the page is redrawn, which is
    the one place Streamlit lets code reset a widget's value: the note and clauses are
    cleared after a verdict is saved and kept if it wasn't, so nothing typed is lost."""
    state = st.session_state
    try:
        verdict = Verdict(
            outcome=state.outcome,
            reviewer=state.reviewer,
            note=state.note,
            expected=split_citations(state.expected),
        )
    except ValidationError as err:
        first = err.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "verdict"
        problem = first["msg"].removeprefix("Value error, ")
        state.review_message = ("error", f"Not recorded ({field}): {problem}")
        return
    try:
        queue.resolve(review_id, verdict)
    except AlreadyResolvedError:
        state.review_message = (
            "warning",
            f"Review #{review_id} already has a verdict; it was left as it is.",
        )
    except ReviewError as err:
        state.review_message = ("error", f"The verdict wasn't saved: {err}")
    else:
        state.review_message = (
            "success",
            f"Recorded: #{review_id} {verdict.outcome} by {verdict.reviewer}.",
        )
        state.note, state.expected = "", ""
        state.reviewer_name = verdict.reviewer  # offered again on the next review


def _verdict_form(queue: ReviewQueue, item: ReviewItem) -> None:
    with st.form("verdict"):
        # Streamlit forgets a widget's value once it isn't drawn (an empty queue hides the
        # form), so the name is also kept under its own key and offered as the default.
        st.text_input(
            "Your name",
            key="reviewer",
            value=st.session_state.get("reviewer_name", ""),
            max_chars=100,
        )
        st.radio("Verdict", list(OUTCOMES), format_func=OUTCOMES.get, key="outcome")
        st.text_area(
            "Note (required for a correction: what the right answer is)", key="note", height=90
        )
        st.text_input(
            "Clauses a correct answer cites, if different (e.g. CD 109 cl. 5.1, CD 109 Table 5.1)",
            key="expected",
        )
        st.form_submit_button(
            "Record verdict", type="primary", on_click=_record, args=(queue, item.id)
        )


def _show_resolved(resolved: list[ReviewItem]) -> None:
    with st.expander(f"Reviewed (latest {len(resolved)})"):
        for item in resolved:
            verdict = item.verdict
            st.markdown(
                f"**#{item.id} {verdict.outcome}** by {safe_markdown(verdict.reviewer)}, "
                f"{item.resolved_at:%Y-%m-%d}: {safe_markdown(item.flagged.question)}"
            )
            if verdict.note:
                st.caption(safe_markdown(verdict.note))
    with st.expander("As golden-set candidates (edit, then paste into evals/golden.yaml)"):
        st.code(candidates(sorted(resolved, key=lambda i: i.id)), language="yaml")


def render(queue: ReviewQueue) -> None:
    st.write(
        "Answers that failed a check, hit a limit, describe a possible departure or came "
        "back with low confidence wait here for an engineer."
    )
    try:
        stats = queue.stats()
        pending = queue.list("pending")
        resolved = queue.list("resolved", limit=RESOLVED_SHOWN)
    except ReviewError as err:
        st.error(f"The review queue can't be read: {err}")
        return
    st.caption(f"{stats.pending} waiting · {stats.approved} approved · {stats.corrected} corrected")
    if message := st.session_state.pop("review_message", None):
        kind, text = message
        MESSAGES[kind](safe_markdown(text))
    if pending:
        by_id = {item.id: item for item in pending}
        chosen = st.selectbox(
            "Waiting for review (oldest first)",
            list(by_id),
            format_func=lambda review_id: _label(by_id[review_id]),
            key="review_item",
        )
        _show_flagged(by_id[chosen])
        _verdict_form(queue, by_id[chosen])
    else:
        st.info("Nothing is waiting for review.")
    if resolved:
        _show_resolved(resolved)
