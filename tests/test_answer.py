import pytest
from pydantic import ValidationError

from agent.answer import Answer

VALID = {
    "answer": "Superelevation shall not exceed 7% [CD 109 cl. 5.3.1].",
    "citations": ["CD109-5.3.1"],
    "requirement_type": "shall",
    "is_departure": None,
    "confidence": "high",
    "needs_human_review": False,
}


def test_parses_model_json():
    answer = Answer.model_validate_json(
        '{"answer": "x [CD 109 cl. 5.3.1]", "citations": ["CD109-5.3.1"], '
        '"requirement_type": "should", "is_departure": false, "confidence": "medium", '
        '"needs_human_review": false}'
    )

    assert answer.requirement_type == "should"
    assert answer.is_departure is False


def test_is_immutable():
    answer = Answer(**VALID)

    with pytest.raises(ValidationError):
        answer.confidence = "low"


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("answer", "   "),
        ("requirement_type", "required"),
        ("confidence", "very high"),
    ],
)
def test_rejects_invalid_fields(field, bad):
    with pytest.raises(ValidationError):
        Answer(**{**VALID, field: bad})


def test_rejects_unexpected_fields():
    with pytest.raises(ValidationError):
        Answer(**VALID, verdict="pass")


def test_schema_is_usable_for_structured_output():
    # Structured outputs need every property required and no extras.
    schema = Answer.model_json_schema()

    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_accepts_must_as_a_statutory_requirement():
    assert Answer(**{**VALID, "requirement_type": "must"}).requirement_type == "must"
