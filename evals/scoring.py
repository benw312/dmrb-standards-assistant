"""Scoring one answer against a golden question, and summarising a run with its denominators.

Definitions (each states which questions it is measured over, its n):

- citation precision: cited clauses that are expected, / clauses cited. In-scope questions
  only. Citing nothing on an in-scope question scores 0, so a pipeline can't look precise by
  refusing more.
- citation recall: expected clauses cited, / expected. In-scope questions only.
- requirement accuracy: governing strength (shall / should / ...) matches. In-scope questions
  that give one.
- departure accuracy: `is_departure` matches. Questions that give `departure`. An answer of
  null ("not about a departure") counts as false: it claims no departure is needed.
- correct refusals: out-of-scope questions answered not_covered *without* escalating. A
  fallback after a crash or time-out is also not_covered; it isn't credited as a refusal.
- false refusals: in-scope questions answered not_covered (lower is better).
- escalation rate, review rate, tokens and seconds: all questions.

Split parts (CD109-5.1#2) count as their clause. A `#dup2` chunk is a second printing of the
same number; the gate already treats it as that number, so scoring does too.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agent.citations import CitationError, normalise_citation
from agent.engine import Mode
from agent.loop import AgentResult
from agent.rag import RagResult


class Question(Protocol):
    id: str
    type: str
    expected: tuple[str, ...]
    requirement: str | None
    departure: bool | None


class Score(BaseModel):
    """One answer's scores. None = the metric doesn't apply to this question."""

    model_config = ConfigDict(frozen=True)

    id: str
    mode: Mode
    citation_precision: float | None
    citation_recall: float | None
    requirement_ok: bool | None
    departure_ok: bool | None
    refused_correctly: bool | None  # out-of-scope questions only
    refused_wrongly: bool | None  # in-scope questions only
    needs_review: bool
    escalated: bool
    input_tokens: int
    output_tokens: int
    seconds: float


def _cited(result: AgentResult | RagResult) -> set[str]:
    cited = set()
    for raw in result.answer.citations:
        try:
            cited.add(normalise_citation(raw.split("#", 1)[0]))
        except CitationError:
            cited.add(raw)  # still counted as cited, and can never match: lowers precision
    return cited


def score(question: Question, result: AgentResult | RagResult, mode: Mode) -> Score:
    a = result.answer
    in_scope = question.type != "out_of_scope"
    cited, expected = _cited(result), set(question.expected)
    hits = len(cited & expected)
    refused = a.requirement_type == "not_covered"
    escalated = bool(result.escalation)
    return Score(
        id=question.id,
        mode=mode,
        citation_precision=(hits / len(cited) if cited else 0.0) if in_scope else None,
        citation_recall=hits / len(expected) if in_scope else None,
        requirement_ok=(
            a.requirement_type == question.requirement
            if in_scope and question.requirement
            else None
        ),
        departure_ok=(
            None if question.departure is None else bool(a.is_departure) is question.departure
        ),
        refused_correctly=None if in_scope else refused and not escalated,
        refused_wrongly=refused if in_scope else None,
        needs_review=result.needs_human_review,
        escalated=escalated,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        seconds=result.seconds,
    )


@dataclass(frozen=True)
class Stat:
    value: float | None  # None when no question in the run applies
    n: int  # questions it was measured over
    count: int | None = None  # for yes/no metrics: how many were yes

    def cell(self, as_count: bool = False) -> str:
        if self.value is None:
            return "n/a"
        if as_count:
            return f"{self.value:,.0f}" if self.value >= 100 else f"{self.value:.1f}"
        if self.count is not None:
            return f"{self.value:.0%} ({self.count}/{self.n})"
        return f"{self.value:.0%} (n={self.n})"


def _stat(values: Iterable[float | bool | None]) -> Stat:
    present = [v for v in values if v is not None]
    if not present:
        return Stat(None, 0)
    flags = all(isinstance(v, bool) for v in present)
    return Stat(mean(float(v) for v in present), len(present), sum(present) if flags else None)


# Metric name -> how to read it off a Score. Counts are shown as numbers, not percentages.
METRICS = {
    "citation precision": lambda s: s.citation_precision,
    "citation recall": lambda s: s.citation_recall,
    "requirement accuracy": lambda s: s.requirement_ok,
    "departure accuracy": lambda s: s.departure_ok,
    "correct refusals": lambda s: s.refused_correctly,
    "false refusals (lower is better)": lambda s: s.refused_wrongly,
    "escalated": lambda s: s.escalated,
    "flagged for review": lambda s: s.needs_review,
    "input tokens / q": lambda s: s.input_tokens,
    "output tokens / q": lambda s: s.output_tokens,
    "seconds / q": lambda s: s.seconds,
}
_COUNTS = {"input tokens / q", "output tokens / q", "seconds / q"}


def summarise(scores: Sequence[Score]) -> dict[str, Stat]:
    return {name: _stat(read(s) for s in scores) for name, read in METRICS.items()}


def format_table(
    by_mode: dict[str, Sequence[Score]], failures: dict[str, int] | None = None
) -> str:
    """Markdown: one row per metric, one column per mode, each cell with its denominator."""
    summaries = {mode: summarise(scores) for mode, scores in by_mode.items()}
    lines = ["| Metric | " + " | ".join(summaries) + " |", "|---|" + "---|" * len(summaries)]
    for name in METRICS:
        cells = [summary[name].cell(as_count=name in _COUNTS) for summary in summaries.values()]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append(
        "| questions answered | " + " | ".join(str(len(s)) for s in by_mode.values()) + " |"
    )
    if failures and any(failures.values()):
        lines.append(
            "| failed to run | " + " | ".join(str(failures.get(m, 0)) for m in by_mode) + " |"
        )
    return "\n".join(lines)
