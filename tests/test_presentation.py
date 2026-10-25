"""Demo display logic: which banner an answer gets, safe rendering, the cost line."""

from agent.rag import FALLBACK_ANSWER
from app.api import to_response
from app.presentation import EXAMPLES, STRENGTH_LABELS, cost_line, safe_markdown, status
from tests.test_api import _agent_result
from tests.test_rag import GOOD


def _response(**overrides):
    return to_response(_agent_result(**overrides), "agent", "r1")


def test_a_clean_answer_claims_only_what_was_checked():
    s = status(_response())
    assert s.kind == "success" and s.show_details and s.reasons == ()
    assert "found in the text retrieved" in s.headline and "verified" not in s.headline.lower()


def test_an_escalated_answer_is_unverified_and_hides_its_flags():
    s = status(_response(escalation=("Answer 2: citation check failed: X",)))
    assert s.kind == "error" and "Unverified" in s.headline and not s.show_details
    assert s.reasons == ("Answer 2: citation check failed: X",)


def test_a_fallback_after_a_failure_is_never_shown_as_not_covered():
    crashed = _response(answer=FALLBACK_ANSWER, escalation=("Stopped at the 90s time budget.",))
    s = status(crashed)
    assert s.kind == "error" and not s.show_details  # escalation wins over not_covered


def test_a_clean_refusal_says_it_found_nothing_not_that_nothing_exists():
    refusal = GOOD.model_copy(
        update={"answer": "Not covered.", "citations": (), "requirement_type": "not_covered"}
    )
    s = status(_response(answer=refusal))
    assert s.kind == "info" and "doesn't confirm no requirement exists" in s.headline


def test_a_departure_is_flagged_for_review_with_the_reason():
    answer = GOOD.model_copy(update={"is_departure": True, "needs_human_review": True})
    s = status(_response(answer=answer))
    assert s.kind == "warning" and s.reasons == ("possible Departure from Standard",)


def test_review_reasons():
    review = GOOD.model_copy(update={"needs_human_review": True})
    assert status(_response(answer=review)).reasons == ("the model asked for review",)
    low = review.model_copy(update={"confidence": "low"})
    assert status(_response(answer=low)).reasons == ("low confidence",)


def test_model_markdown_cannot_load_images_or_render_latex():
    text = "See ![x](https://evil.example/p.png) and costs $5 to $10."
    assert safe_markdown(text) == r"See [image removed] and costs \$5 to \$10."


def test_cost_line_counts_tools():
    assert cost_line(_response()) == "agent · 1 tool call · 800 in / 90 out tokens · 4.2s"
    assert "· 0 tool calls ·" in cost_line(_response(tool_calls=()))


def test_every_strength_has_a_label_and_examples_are_questions():
    assert {"must", "shall", "should", "may", "note", "not_covered"} <= set(STRENGTH_LABELS)
    assert all(q.endswith("?") for q in EXAMPLES)
