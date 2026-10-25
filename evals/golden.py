"""Phase 6: score RAG, agent and graph answers against Ben's golden set (evals/golden.yaml).

    uv run python -m evals.golden                         # all three modes, every question
    uv run python -m evals.golden --modes agent --limit 5 # a partial run, labelled as such

Each question says what a correct answer cites, the strength of the governing clause, whether
the design described needs a departure, and whether it is out of scope. Metric definitions
and their denominators are in evals/scoring.py; the table shows each one's n.

Before anything is billed, every expected clause is checked against data/chunks/, so a typo
can't quietly cap a question's recall. A question that fails to run (an Azure error, a bug)
is counted as a failure and the run carries on. Each mode's answers are written to
evals/results/<timestamp>-<mode>.jsonl (git-ignored: they quote the standard) for reading and
for the azure-ai-evaluation judges.
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent.answer import RequirementType
from agent.citations import CitationError, normalise_citation
from agent.engine import Answerer, Mode
from evals.scoring import Score, format_table, score
from evals.yaml_io import first_error, read_yaml_list

logger = logging.getLogger(__name__)

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
QuestionType = Literal["lookup", "multi_hop", "departure", "calculation", "out_of_scope"]
MODES: tuple[Mode, ...] = ("rag", "agent", "graph")
_FIELDS = "id, query, type, expected, requirement, departure, answer, notes"


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    type: QuestionType
    expected: tuple[str, ...] = ()  # chunk citation IDs after validation; empty if out of scope
    requirement: RequirementType | None = None  # strength of the governing clause
    departure: bool | None = None  # would the described design need a departure?
    answer: str | None = None  # what a good answer says, for judges and for reading
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _not_template(cls, qid: str) -> str:
        if qid.upper().startswith("EXAMPLE"):
            raise ValueError("this is the template's example: give it your own id")
        return qid

    @field_validator("query")
    @classmethod
    def _not_blank(cls, query: str) -> str:
        if not query.strip():
            raise ValueError("query is blank")
        return query.strip()

    @field_validator("expected", mode="before")
    @classmethod
    def _normalise(cls, raw: object) -> object:
        if raw is None:
            return ()
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list | tuple):
            raise ValueError("list the clauses: [CD 109 cl. 4.5, CD 109 Table 5.1]")
        try:
            return tuple(dict.fromkeys(normalise_citation(str(r)) for r in raw))
        except CitationError as err:
            raise ValueError(str(err)) from None

    @model_validator(mode="after")
    def _consistent(self) -> "GoldenQuestion":
        # Model-level, so it runs when fields are left out too.
        if self.type == "out_of_scope":
            if self.expected or self.departure is not None or self.requirement:
                raise ValueError(
                    "an out_of_scope question has no expected, requirement or departure: "
                    "the right answer is 'not covered'"
                )
            return self
        if not self.expected:
            raise ValueError("expected: list at least one clause a correct answer cites")
        if self.requirement == "not_covered":
            raise ValueError("requirement not_covered is only for out_of_scope questions")
        if self.type == "departure" and self.departure is None:
            raise ValueError("a departure question needs departure: true or false")
        return self


class GoldenError(ValueError):
    """evals/golden.yaml is missing or malformed."""


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenQuestion]:
    """Every question, or one error listing every bad entry by position and id."""
    rows = read_yaml_list(path, GoldenError)
    questions, problems, seen_ids, seen_queries = [], [], set(), {}
    for number, row in enumerate(rows, start=1):
        qid = row.get("id") if isinstance(row, dict) else None
        where = f"question {number}" + (f" ({qid})" if isinstance(qid, str) else "")
        try:
            question = GoldenQuestion.model_validate(row)
        except ValidationError as err:
            problems.append(f"{where}: {first_error(err, _FIELDS)}")
            continue
        if question.id in seen_ids:
            problems.append(f"{where}: duplicate id {question.id!r}")
        if (same := seen_queries.get(question.query.casefold())) is not None:
            problems.append(f"{where}: same query as {same}")
        seen_ids.add(question.id)
        seen_queries[question.query.casefold()] = question.id
        questions.append(question)
    if problems:
        raise GoldenError(f"{path.name}:\n  " + "\n  ".join(problems))
    if not questions:
        raise GoldenError(f"{path.name} has no questions yet")
    return questions


def unknown_expected(questions: Sequence[GoldenQuestion], citation_ids: set[str]) -> list[str]:
    """'G04: CD109-5.1.9' for each expected clause no chunk carries (a typo, or lost)."""
    return [f"{q.id}: {e}" for q in questions for e in q.expected if e not in citation_ids]


def evaluate(
    questions: Sequence[GoldenQuestion],
    answerer: Answerer,
    mode: Mode,
    sink: Callable[[dict], None] = lambda row: None,
) -> tuple[list[Score], int]:
    """Answer and score each question. Returns the scores and how many failed to run.

    A question that raises is logged, recorded and skipped, so one failure late in a long,
    billed run doesn't lose the rest.
    """
    scores, failures = [], 0
    for question in questions:
        try:
            result = answerer(question.query, mode)
        except Exception as err:  # noqa: BLE001 - recorded and counted, never silent
            logger.exception("%s %s failed", mode, question.id)
            sink({"question": question.model_dump(), "error": f"{type(err).__name__}: {err}"})
            failures += 1
            continue
        s = score(question, result, mode)
        sink(
            {
                "question": question.model_dump(),
                "result": result.model_dump(mode="json"),
                "score": s.model_dump(),
            }
        )
        scores.append(s)
    return scores, failures


class JsonlSink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def __call__(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wires live Azure clients
    from agent.engine import build_live_answerer
    from agent.rag import RagError
    from core.settings import SettingsError
    from ingest.chunk import read_chunks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--limit", type=_positive, help="only the first N questions (partial)")
    parser.add_argument("--questions", type=Path, default=GOLDEN_PATH)
    args = parser.parse_args(argv)
    try:
        questions = load_golden(args.questions)
        if missing := unknown_expected(questions, {c.citation_id for c in read_chunks()}):
            print("Expected clauses not in data/chunks/ (fix before spending):")
            print("\n".join(f"  {m}" for m in missing))
            return 1
        answerer = build_live_answerer()
    except (GoldenError, SettingsError, RagError) as err:
        print(f"Error: {err}")
        return 1
    total = len(questions)
    questions = questions[: args.limit]
    stamp = f"{datetime.now(UTC):%Y%m%d-%H%M%S}"
    by_mode, failures = {}, {}
    for mode in args.modes:
        print(f"{mode}: {len(questions)} questions ...", flush=True)
        sink = JsonlSink(RESULTS_DIR / f"{stamp}-{mode}.jsonl")
        by_mode[mode], failures[mode] = evaluate(questions, answerer, mode, sink)
    if len(questions) < total:
        print(f"PARTIAL RUN: first {len(questions)} of {total} questions; not for the README.")
    print(format_table(by_mode, failures))
    print(f"\nAnswers: {RESULTS_DIR}/{stamp}-<mode>.jsonl")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
