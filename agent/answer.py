"""The structured answer the model must return, validated before anything trusts it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RequirementType = Literal["shall", "should", "may", "note", "not_covered"]
Confidence = Literal["high", "medium", "low"]


class Answer(BaseModel):
    # extra="forbid" also emits additionalProperties: false, which structured outputs require.
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str = Field(description="Plain-English answer citing each claim as [CD 109 cl. 5.3.1]")
    citations: tuple[str, ...] = Field(
        description="IDs of the clauses relied on, exactly as given, e.g. 'CD109-5.3.1'"
    )
    requirement_type: RequirementType = Field(
        description="Strength of the governing clause, or 'not_covered' if the clauses provided "
        "do not answer the question"
    )
    is_departure: bool | None = Field(
        description="Whether the described design would need a Departure from Standard; "
        "null when the question is not about one"
    )
    confidence: Confidence
    needs_human_review: bool

    @field_validator("answer")
    @classmethod
    def answer_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must not be blank")
        return value
