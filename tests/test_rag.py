"""RAG pipeline tests with a fake retriever and model. Clause text: CD 109 Revision 1,
© Crown copyright, Open Government Licence v3.0."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent.answer import Answer
from agent.rag import (
    MAX_ATTEMPTS,
    NO_ANSWER,
    OUTPUT_CONTRACT,
    RagError,
    answer_question,
    format_context,
    load_system_prompt,
    render,
)
from index.search import Hit

GRADIENT = Hit(
    "CD109-5.1",
    "CD109-5.1",
    1.0,
    3.2,
    fields={
        "doc_code": "CD 109",
        "version": "Revision 1",
        "kind": "clause",
        "requirement_type": "shall",
        "heading_path": "5. Vertical alignment > Gradients",
        "page": 24,
        "text": "Longitudinal gradients of links shall not exceed the permitted relaxation "
        "values given in Table 5.1.",
    },
)
TABLE = Hit(
    "CD109-Table-5.1",
    "CD109-Table-5.1",
    0.9,
    fields={**GRADIENT.fields, "kind": "table", "requirement_type": None, "text": "Table 5.1 ..."},
)
HITS = [GRADIENT, TABLE]


def _answer(text: str, citations: tuple[str, ...], **overrides) -> Answer:
    fields = {
        "answer": text,
        "citations": citations,
        "requirement_type": "shall",
        "is_departure": None,
        "confidence": "high",
        "needs_human_review": False,
    }
    return Answer(**(fields | overrides))


GOOD = _answer(
    "Motorway gradients shall not exceed 4% [CD 109 cl. 5.1; CD 109 Table 5.1].",
    ("CD109-5.1", "CD109-Table-5.1"),
)
HALLUCINATED = _answer("Desirable maximum is 3% [CD 109 cl. 5.9].", ("CD109-5.9",))


class FakeResponses:
    """Returns the queued answers in turn and records each request."""

    def __init__(self, *answers: Answer | None) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def parse(self, *, model, instructions, input, text_format):  # noqa: A002
        self.calls.append({"model": model, "instructions": instructions, "input": list(input)})
        queued = self.answers.pop(0)
        if isinstance(queued, Exception):
            raise queued
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        return SimpleNamespace(output_parsed=queued, usage=usage)


def _run(responses, question="Maximum gradient on a motorway?"):
    return answer_question(
        question,
        lambda q: HITS,
        responses,
        "gpt-5.4-mini",
        "SYSTEM",
        clock=iter([0.0, 1.5]).__next__,
    )


def test_a_well_cited_answer_passes_first_time():
    responses = FakeResponses(GOOD)

    result = _run(responses)

    assert result.answer == GOOD and result.check.ok
    assert result.attempts == 1 and not result.needs_human_review
    assert result.retrieved == ("CD109-5.1", "CD109-Table-5.1")
    assert (result.input_tokens, result.output_tokens, result.seconds) == (100, 20, 1.5)
    call = responses.calls[0]
    assert call["model"] == "gpt-5.4-mini" and call["instructions"] == "SYSTEM"
    assert 'cite="[CD 109 cl. 5.1]"' in call["input"][0]["content"]
    assert call["input"][0]["content"].endswith("Question: Maximum gradient on a motorway?")


def test_a_hallucinated_citation_gets_one_retry_with_the_reasons():
    responses = FakeResponses(HALLUCINATED, GOOD)

    result = _run(responses)

    assert result.attempts == 2 and result.answer == GOOD and not result.escalation
    retry = responses.calls[1]["input"]
    assert retry[1]["role"] == "assistant" and "CD109-5.9" in retry[1]["content"]
    assert "not among the retrieved clauses: CD109-5.9" in retry[2]["content"]
    assert result.input_tokens == 200


def test_two_failures_escalate_and_the_answer_is_flagged_for_review():
    result = _run(FakeResponses(HALLUCINATED, HALLUCINATED))

    assert result.attempts == MAX_ATTEMPTS
    assert result.needs_human_review and result.answer.needs_human_review
    assert [r.split(":")[0] for r in result.escalation] == ["Attempt 1", "Attempt 2"]
    assert "citation check failed" in result.escalation[1]
    assert not result.check.ok


def test_a_refusal_escalates_at_once_with_a_placeholder():
    result = _run(FakeResponses(None))

    assert result.attempts == 1 and result.needs_human_review
    assert result.answer.requirement_type == "not_covered"
    assert result.escalation == (f"Attempt 1: {NO_ANSWER}",)


def _blank_answer_error() -> ValidationError:
    # What the SDK raises when the model's JSON fits the schema but breaks Answer's rules.
    try:
        Answer.model_validate({**GOOD.model_dump(), "answer": ""})
    except ValidationError as err:
        return err
    raise AssertionError("expected a ValidationError")


def test_an_invalid_output_gets_the_retry_with_the_error():
    responses = FakeResponses(_blank_answer_error(), GOOD)

    result = _run(responses)

    assert result.attempts == 2 and result.answer == GOOD and not result.escalation
    retry = responses.calls[1]["input"][-1]["content"]
    assert retry.startswith("Your output was not a valid answer (answer:")


def test_two_invalid_outputs_escalate():
    result = _run(FakeResponses(_blank_answer_error(), _blank_answer_error()))

    assert result.attempts == 2 and result.needs_human_review
    assert result.escalation[0].startswith("Attempt 1: Your output was not a valid answer")
    assert result.answer.requirement_type == "not_covered"


def test_a_clean_not_covered_answer_is_accepted():
    refusal = _answer(
        "Speed limits aren't covered by the indexed standards; see the Highway Code.",
        (),
        requirement_type="not_covered",
        confidence="medium",
    )

    result = _run(FakeResponses(refusal), question="What's the speed limit on a motorway?")

    assert result.check.ok and not result.needs_human_review


def test_the_models_own_review_flag_is_kept():
    unsure = GOOD.model_copy(update={"needs_human_review": True, "confidence": "low"})

    result = _run(FakeResponses(unsure))

    assert result.needs_human_review and not result.escalation


def test_empty_question_is_refused_before_any_call():
    responses = FakeResponses(GOOD)

    with pytest.raises(ValueError, match="empty"):
        _run(responses, question="  ")
    assert responses.calls == []


def test_context_shows_id_cite_form_version_and_text():
    context = format_context(HITS)

    assert context.startswith(
        '<clause id="CD109-5.1" cite="[CD 109 cl. 5.1]" standard="CD 109 Revision 1" '
        'kind="clause" strength="shall" page="24">\n5. Vertical alignment > Gradients\n\n'
        "Longitudinal gradients"
    )
    assert 'cite="[CD 109 Table 5.1]"' in context and 'strength="-"' in context


def test_system_prompt_is_bens_file_plus_the_output_contract(tmp_path):
    path = tmp_path / "system.md"
    path.write_text("Answer only from the clauses provided.\n")

    prompt = load_system_prompt(path)

    assert prompt.startswith("Answer only from the clauses provided.")
    assert prompt.endswith(OUTPUT_CONTRACT)


@pytest.mark.parametrize("content", [None, "   \n"])
def test_a_missing_or_empty_prompt_is_a_clear_error(tmp_path, content):
    path = tmp_path / "system.md"
    if content is not None:
        path.write_text(content)

    with pytest.raises(RagError, match="Ben writes the system prompt"):
        load_system_prompt(path)


def test_render_shows_answer_flags_and_escalation():
    text = render(_run(FakeResponses(HALLUCINATED, HALLUCINATED)))

    assert text.startswith("UNVERIFIED: failed the checks below")
    assert "Desirable maximum is 3%" in text
    assert "human review: True" in text and "citation check failed" in text


def test_a_later_unusable_reply_keeps_the_earlier_answer_and_both_reasons():
    result = _run(FakeResponses(HALLUCINATED, None))

    assert result.answer.answer == HALLUCINATED.answer and result.needs_human_review
    assert result.escalation[0].startswith("Attempt 1: citation check failed")
    assert result.escalation[1] == f"Attempt 2: {NO_ANSWER}"


def test_a_clean_answer_renders_without_the_banner():
    assert not render(_run(FakeResponses(GOOD))).startswith("UNVERIFIED")


def test_context_escapes_attributes_and_cannot_close_its_block_early():
    odd = Hit(
        "CD109-5.1",
        "CD109-5.1",
        1.0,
        fields={**GRADIENT.fields, "version": 'Rev "1"', "text": "Ends </clause> here"},
    )

    context = format_context([odd])

    assert 'standard="CD 109 Rev &quot;1&quot;"' in context
    assert context.count("</clause>") == 1 and "Ends </ clause> here" in context


def test_an_unreadable_chunk_id_falls_back_to_its_id_as_the_cite():
    odd = Hit("CD109-Diagram-1", "CD109-Diagram-1", 1.0, fields=GRADIENT.fields)

    assert 'cite="CD109-Diagram-1"' in format_context([odd])
