"""Adding a question to evals/golden.yaml safely: the logic behind evals/golden_editor.py.

The golden set is ground truth, written by an engineer. This module only helps with the
typing: it formats a question in the file's layout, checks it, and appends it. It never
calls a model and never suggests an answer, a strength or a departure flag, because ground
truth drafted by the system under test would only measure the system against itself.

Appending is all it does to the file: the bytes already there, comments and line endings
included, are never rewritten. The new file is built beside the old one, read back with the
real loader, and swapped in only if every earlier question is unchanged and the new one reads
back exactly as entered.
"""

import os
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

from agent.citations import CitationError, display_citation, normalise_citation
from evals.golden import GOLDEN_PATH, GoldenError, GoldenQuestion, QuestionType, load_golden
from evals.yaml_io import read_yaml_list
from ingest.models import Chunk

GOLDEN_ENV = "GOLDEN_FILE"  # another file to edit instead (a scratch copy, a test)
TARGET = (30, 50)  # questions, from PLAN.md
ID_PREFIX = "G"
SEARCH_LIMIT = 10
MAX_WORD_COUNT = 3  # per word: a long clause repeating a word isn't more relevant

TYPES: dict[QuestionType, str] = {
    "lookup": "one clause or table answers it",
    "multi_hop": "needs a clause plus what it points to (a table, another clause)",
    "departure": "describes a design that may not comply",
    "calculation": "needs an equation (superelevation, transition length, VISI ...)",
    "out_of_scope": "the indexed standards don't cover it; the right answer is 'not covered'",
}


def golden_path() -> Path:
    return Path(os.environ.get(GOLDEN_ENV) or GOLDEN_PATH)


def load_or_empty(path: Path) -> list[GoldenQuestion]:
    """The questions so far; [] while the file holds only the template comments.
    Raises GoldenError if the file is missing or any existing question is malformed."""
    return load_golden(path) if read_yaml_list(path, GoldenError) else []


def next_id(questions: Iterable[GoldenQuestion]) -> str:
    """'G07' after G06. Numbers already used are never handed out again, gaps included."""
    used = [
        int(match[1])
        for q in questions
        if (match := re.fullmatch(rf"{ID_PREFIX}(\d+)", q.id, flags=re.IGNORECASE))
    ]
    return f"{ID_PREFIX}{max(used, default=0) + 1:02d}"


# Ids like G01 are written bare, as in the file's examples. Anything else is quoted: YAML
# reads a bare `no`, `1` or `null` as something other than text.
_PLAIN_ID = re.compile(r"[A-Za-z]+\d+")


def _quoted(text: str) -> str:
    # PyYAML does the escaping, so quotes, colons, # and odd characters can't break the file.
    return yaml.safe_dump(text, default_style='"', allow_unicode=True, width=float("inf")).strip()


def _cite(chunk_id: str) -> str:
    return display_citation(chunk_id).strip("[]")  # 'CD 109 cl. 5.1', as the file writes them


def format_block(question: GoldenQuestion) -> str:
    """The question as a block in golden.yaml's layout (same field order as its examples)."""
    lines = [
        f"- id: {question.id if _PLAIN_ID.fullmatch(question.id) else _quoted(question.id)}",
        f"  query: {_quoted(question.query)}",
        f"  type: {question.type}",
    ]
    if question.expected:
        lines.append(f"  expected: [{', '.join(_cite(c) for c in question.expected)}]")
    if question.requirement:
        lines.append(f"  requirement: {question.requirement}")
    if question.departure is not None:
        lines.append(f"  departure: {str(question.departure).lower()}")
    if question.answer:
        lines.append(f"  answer: {_quoted(question.answer)}")
    if question.notes:
        lines.append(f"  notes: {_quoted(question.notes)}")
    return "\n".join(lines) + "\n"


def problems(
    question: GoldenQuestion, existing: Sequence[GoldenQuestion], citation_ids: set[str] | None
) -> list[str]:
    """Why this question can't be added yet, in plain words. `citation_ids` is every clause
    in data/chunks/, or None if the chunks aren't on this machine (then that check is skipped)."""
    found = []
    if any(q.id.casefold() == question.id.casefold() for q in existing):
        found.append(f"id {question.id} is already used")
    same = [q.id for q in existing if q.query.casefold() == question.query.casefold()]
    if same:
        found.append(f"{same[0]} already asks this question")
    if citation_ids is not None:
        missing = [_cite(c) for c in question.expected if c not in citation_ids]
        if missing:
            found.append(f"not in the indexed standards: {', '.join(missing)}")
    if not (question.answer or "").strip():
        found.append("answer: say what a good answer says, in a sentence or two")
    return found


def append_question(path: Path, question: GoldenQuestion) -> None:
    """Add the question to the end of the file, or raise GoldenError and change nothing.

    Works on the file's bytes, so what was there before is the exact start of what is there
    after: line endings (a file saved on Windows), a byte-order mark and trailing blank lines
    all stay as they were."""
    path = path.resolve()  # a symlinked file is updated where it really lives
    before = load_or_empty(path)
    try:
        original = path.read_bytes()
        if not os.access(path, os.W_OK):
            raise GoldenError(f"{path.name} is read-only; nothing was saved")
        newline = "\r\n" if b"\r\n" in original else "\n"
        gap = newline if original.endswith((b"\n", b"\r")) else newline * 2
        block = (gap + format_block(question).replace("\n", newline)).encode("utf-8")
        # A name of its own, so two saves at once can't write over each other's draft.
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as handle:
            draft = Path(handle.name)
            handle.write(original + block)
    except OSError as err:
        raise GoldenError(f"couldn't write {path.name}: {err}") from err
    try:
        shutil.copymode(path, draft)
        try:
            after = load_golden(draft)
        except GoldenError as err:
            # The loader names the draft; the reader only knows golden.yaml.
            raise GoldenError(
                f"{question.id} would stop {path.name} loading, so nothing was saved: "
                + str(err).replace(draft.name, path.name)
            ) from None
        if after != [*before, question]:
            raise GoldenError(f"{question.id} didn't read back as entered; nothing was saved")
        if path.read_bytes() != original:
            raise GoldenError(f"{path.name} changed while saving; nothing was saved, try again")
        os.replace(draft, path)  # one step: the file is the old one or the new one, never half
    except OSError as err:
        raise GoldenError(f"couldn't write {path.name}: {err}") from err
    finally:
        draft.unlink(missing_ok=True)


def counts(questions: Iterable[GoldenQuestion]) -> dict[QuestionType, int]:
    tally = Counter(q.type for q in questions)
    return {kind: tally.get(kind, 0) for kind in TYPES}


def _as_citation(text: str) -> str | None:
    try:
        return normalise_citation(text)
    except CitationError:
        return None


def search_chunks(chunks: Sequence[Chunk], text: str, limit: int = SEARCH_LIMIT) -> list[Chunk]:
    """Find clauses to cite, offline. A citation ('CD 109 cl. 5.1', 'CD109-Table-5.1') finds
    that clause and its sub-clauses; anything else is words that must all appear, best first.
    One result per citation: a long clause split into parts shows once."""
    text = text.strip()
    if not text:
        return []
    by_citation: dict[str, Chunk] = {}
    for chunk in chunks:
        by_citation.setdefault(chunk.citation_id, chunk)  # the first part of a split clause
    if wanted := _as_citation(text):
        exact = [c for cid, c in by_citation.items() if cid == wanted]
        below = [c for cid, c in by_citation.items() if cid.startswith(wanted + ".")]
        return [*exact, *below][:limit]
    words = text.casefold().split()
    scored = []
    for chunk in chunks:
        haystack = f"{chunk.doc_code} {chunk.label} {chunk.heading_path} {chunk.text}".casefold()
        if all(word in haystack for word in words):
            heading = chunk.heading_path.casefold()
            # Words in the heading say what the clause is about; in the body, how much.
            score = sum(10 * (w in heading) + min(haystack.count(w), MAX_WORD_COUNT) for w in words)
            # A glossary mentions everything, so it goes last however often it matches.
            scored.append((score if chunk.kind != "glossary" else -1, chunk))
    best: dict[str, Chunk] = {}
    for _, chunk in sorted(scored, key=lambda pair: -pair[0]):
        best.setdefault(chunk.citation_id, by_citation[chunk.citation_id])
    return list(best.values())[:limit]
