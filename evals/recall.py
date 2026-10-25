"""Retrieval recall@k on Ben's questions in evals/retrieval.yaml, for each search mode.

    uv run python -m evals.recall            # recall@5, all four modes
    uv run python -m evals.recall --k 8 --modes semantic hybrid

A question is a hit at k if any of its `expected` clauses is among the top k distinct clauses
returned; split parts of a long clause (CD109-5.3.1#2) count as the clause. Coverage@k is the
share of all expected clauses found. Stops before searching if an expected clause doesn't exist
or the live index is out of date with data/chunks/. Prints a Markdown table for the
README. Costs: one embedding per question and one semantic query per question (free tier
allows 1,000 a month).
"""

import argparse
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent.citations import CitationError, normalise_citation
from evals.yaml_io import first_error, read_yaml_list
from index.search import Hit, Mode

QUESTIONS_PATH = Path(__file__).resolve().parent / "retrieval.yaml"
DEFAULT_K = 5
TARGET_RECALL = 0.8  # below this, fix chunking or the embedding text before Phase 3
# Chunks fetched per search before collapsing split parts, so k distinct clauses remain.
FETCH_POOL = 20
MAX_INDEX_DOCS = 100_000  # upper bound when listing every ID in the index


_LIST_HINT = "write [CD 109 cl. 4.5], or put each clause on its own line starting with '- '"
_FIELDS = "id, query, expected, notes"


class RetrievalQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # str fields are strict on purpose: YAML turns unquoted `no` into False and `1.10` into 1.1,
    # and rejecting those beats silently storing the wrong value.
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    expected: tuple[str, ...] = Field(min_length=1)  # chunk citation IDs after validation
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _not_template(cls, qid: str) -> str:
        if qid.upper().startswith("EXAMPLE"):
            raise ValueError("this is the template's example: give it your own id and clauses")
        return qid

    @field_validator("query")
    @classmethod
    def _not_blank(cls, query: str) -> str:
        if not query.strip():
            raise ValueError("query is blank")
        return query

    @field_validator("expected", mode="before")
    @classmethod
    def _normalise(cls, raw: object) -> object:
        # "CD 109 cl. 4.5" / "CD 109 Table 3.5" -> "CD109-4.5" / "CD109-Table-3.5"
        if isinstance(raw, str):
            raw = [raw]  # `expected: CD 109 cl. 4.5` is unambiguous, so accept it
        if not isinstance(raw, list | tuple) or not raw:
            raise ValueError(f"list at least one clause: {_LIST_HINT}")
        try:
            return tuple(dict.fromkeys(normalise_citation(str(r)) for r in raw))
        except CitationError as err:
            raise ValueError(str(err)) from None


class QuestionsError(ValueError):
    """evals/retrieval.yaml is missing or malformed."""


def load_questions(path: Path = QUESTIONS_PATH) -> list[RetrievalQuestion]:
    """Read the YAML list of questions; report every bad entry at once, by position and id."""
    rows = read_yaml_list(path, QuestionsError)
    questions, problems, seen = [], [], set()
    for number, row in enumerate(rows, start=1):
        qid = row.get("id") if isinstance(row, dict) else None
        # Only show the id as typed: a coerced one (`no` -> False) would confuse more than help.
        where = f"question {number}" + (f" ({qid})" if isinstance(qid, str) else "")
        try:
            question = RetrievalQuestion.model_validate(row)
        except ValidationError as err:
            problems.append(f"{where}: {first_error(err, _FIELDS)}")
            continue
        if question.id in seen:
            problems.append(f"{where}: duplicate id {question.id!r}")
        seen.add(question.id)
        questions.append(question)
    if problems:
        raise QuestionsError(f"{path.name}:\n  " + "\n  ".join(problems))
    if not questions:
        raise QuestionsError(f"{path.name} has no questions")
    return questions


def unknown_expected(
    questions: Iterable[RetrievalQuestion], citation_ids: set[str]
) -> dict[str, tuple[str, ...]]:
    """Expected clauses that no chunk carries: a typo, or a clause the chunker lost.

    Either way the question can never be a hit, so it's reported before any search runs.
    """
    missing = {q.id: tuple(e for e in q.expected if e not in citation_ids) for q in questions}
    return {qid: ids for qid, ids in missing.items() if ids}


@dataclass(frozen=True)
class QuestionResult:
    question_id: str
    rank: int | None  # 1-based rank of the first expected clause in `retrieved`, or None
    coverage: float  # share of the expected clauses that are in `retrieved`
    retrieved: tuple[str, ...]  # the top k distinct citation IDs, best first


def score(question: RetrievalQuestion, hits: Sequence[Hit], k: int) -> QuestionResult:
    """Score the top `k` distinct clauses in `hits` (split parts collapse into their clause).

    `hits` should be a wider pool than k chunks (see FETCH_POOL): otherwise a mode that returns
    several parts of one clause would see fewer than k clauses and be penalised for it.
    """
    retrieved = tuple(dict.fromkeys(h.citation_id for h in hits))[:k]
    expected = set(question.expected)
    rank = next((i for i, cid in enumerate(retrieved, start=1) if cid in expected), None)
    coverage = len(expected & set(retrieved)) / len(expected)
    return QuestionResult(question.id, rank, coverage, retrieved)


def recall(results: Sequence[QuestionResult]) -> float:
    """Share of questions with at least one expected clause in their top k."""
    _require(results)
    return sum(r.rank is not None for r in results) / len(results)


def mean_reciprocal_rank(results: Sequence[QuestionResult]) -> float:
    """MRR@k: a miss within the top k scores 0."""
    _require(results)
    return sum(1 / r.rank for r in results if r.rank) / len(results)


def mean_coverage(results: Sequence[QuestionResult]) -> float:
    """Mean share of expected clauses found. Below recall when multi-clause answers lose parts."""
    _require(results)
    return sum(r.coverage for r in results) / len(results)


def _require(results: Sequence[QuestionResult]) -> None:
    if not results:
        raise ValueError("no results to score")


def fetch_size(k: int) -> int:
    return max(FETCH_POOL, 2 * k)


SearchFn = Callable[[str, Sequence[float], Mode, int], list[Hit]]


def evaluate(
    questions: Sequence[RetrievalQuestion],
    vectors: Sequence[Sequence[float]],
    modes: Sequence[Mode],
    k: int,
    run_search: SearchFn,
) -> dict[Mode, list[QuestionResult]]:
    """Run every question in every mode. `vectors[i]` is the embedding of `questions[i]`."""
    if len(vectors) != len(questions):
        raise ValueError(f"{len(questions)} questions but {len(vectors)} vectors")
    return {
        mode: [
            score(q, run_search(q.query, v, mode, fetch_size(k)), k)
            for q, v in zip(questions, vectors, strict=True)
        ]
        for mode in modes
    }


def index_mismatch(local_ids: set[str], indexed_ids: set[str]) -> str | None:
    """Why the live index doesn't hold exactly the local chunks, or None if it does."""
    if local_ids == indexed_ids:
        return None
    stale, absent = sorted(indexed_ids - local_ids), sorted(local_ids - indexed_ids)
    return (
        f"index differs from data/chunks ({len(absent)} chunks not indexed, {len(stale)} stale "
        f"documents, e.g. {(absent + stale)[:3]}): run python -m index.rebuild"
    )


def format_report(
    questions: Sequence[RetrievalQuestion], results: dict[Mode, list[QuestionResult]], k: int
) -> str:
    """Markdown: rank of the first hit per question and mode, then the summary metrics."""
    modes = list(results)
    by_id = {mode: {r.question_id: r for r in rs} for mode, rs in results.items()}
    head = ["Question", "Expected", *[m.value for m in modes]]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for q in questions:
        ranks = [str(by_id[m][q.id].rank or "miss") for m in modes]
        lines.append(f"| {q.id} | {', '.join(q.expected)} | " + " | ".join(ranks) + " |")
    summary = [
        (f"**recall@{k}**", lambda rs: f"**{recall(rs):.0%}**"),
        (f"MRR@{k}", lambda rs: f"{mean_reciprocal_rank(rs):.2f}"),
        (f"coverage@{k}", lambda rs: f"{mean_coverage(rs):.0%}"),
    ]
    for label, cell in summary:
        lines.append(f"| {label} | | " + " | ".join(cell(results[m]) for m in modes) + " |")
    lines.append(
        f"\nrecall@{k}: share of questions with any expected clause in the top {k} distinct "
        f"clauses. coverage@{k}: share of all expected clauses found."
    )
    return "\n".join(lines)


def misses(questions: Sequence[RetrievalQuestion], results: Sequence[QuestionResult]) -> list[str]:
    """One line per missed question: what came back instead, to see why it missed."""
    queries = {q.id: q for q in questions}
    return [
        f"{r.question_id} {queries[r.question_id].query!r}: expected "
        f"{', '.join(queries[r.question_id].expected)}; got {', '.join(r.retrieved) or 'nothing'}"
        for r in results
        if r.rank is None
    ]


def headline_mode(modes: Sequence[Mode]) -> Mode:
    """The mode whose misses are listed: semantic (production) if run, else the last given."""
    return Mode.SEMANTIC if Mode.SEMANTIC in modes else modes[-1]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="cut-off rank (default 5)")
    parser.add_argument(
        "--modes", nargs="+", type=Mode, default=list(Mode), choices=list(Mode), metavar="MODE"
    )
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be at least 1")
    return args


def _indexed_ids(client) -> set[str]:  # pragma: no cover - live Search call
    return {doc["id"] for doc in client.search(search_text="*", select=["id"], top=MAX_INDEX_DOCS)}


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wires live Azure clients
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    from core.clients import embedding_client
    from core.settings import SettingsError, load_settings
    from index.embed import embed_texts
    from index.search import search
    from ingest.chunk import read_chunks

    args = _parse_args(argv)
    try:
        questions = load_questions(args.questions)
        settings = load_settings()
        chunks = read_chunks()
    except (QuestionsError, SettingsError, ValueError) as err:
        print(f"Error: {err}")
        return 1

    # A question that can never hit would read as a retrieval failure, so stop instead.
    if unknown := unknown_expected(questions, {c.citation_id for c in chunks}):
        for qid, ids in unknown.items():
            print(f"Error: {qid} expects {', '.join(ids)}, which no chunk carries")
        return 1

    credential = DefaultAzureCredential()
    client = SearchClient(settings.search_endpoint, settings.search_index, credential=credential)
    try:
        if problem := index_mismatch({c.id for c in chunks}, _indexed_ids(client)):
            print(f"Error: {problem}")
            return 1
        embeddings = embedding_client(settings, credential).embeddings
        queries = [q.query for q in questions]
        vectors = embed_texts(embeddings, settings.embedding_deployment, queries)
        results = evaluate(
            questions,
            vectors,
            args.modes,
            args.k,
            lambda query, vector, mode, top: search(client, query, vector, mode, top=top),
        )
    except AzureError as err:
        print(f"Error: Azure call failed ({type(err).__name__}): {str(err).splitlines()[0]}")
        return 1

    print(format_report(questions, results, args.k))
    best = headline_mode(args.modes)
    print(f"\nMisses ({best}):")
    print("\n".join(misses(questions, results[best])) or "none")
    if recall(results[best]) < TARGET_RECALL:
        print(f"\n{best} recall@{args.k} is below {TARGET_RECALL:.0%}: fix before Phase 3.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
