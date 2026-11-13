"""Phase 5: the engineer review queue. Flagged answers wait here for a verdict.

An answer is flagged when a check failed, a limit was hit, it describes a possible Departure
from Standard, or the model's confidence is low. Each one is stored with why it was flagged.
An engineer then records a verdict: `approved` (right as given) or `corrected` (wrong, with
what the right answer is). A verdict is written once and never changed, so the queue is also
the audit trail; review/export.py turns verdicts into golden-set candidates.

Storage is one SQLite file (data/reviews.sqlite, git-ignored): enough for a single-user tool
and nothing to run. Every operation opens its own connection, because the API calls this
from worker threads and a sqlite3 connection belongs to the thread that made it.

This module knows nothing about the API or the UI: it stores a `Flagged` snapshot, which the
app layer builds from its response.
"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.answer import Confidence, RequirementType
from agent.citations import CitationError, normalise_citation

REVIEW_DB = Path(__file__).resolve().parent.parent / "data" / "reviews.sqlite"
MAX_NAME_CHARS = 100
MAX_NOTE_CHARS = 2000
MAX_LISTED = 200  # rows returned by one list() call

Outcome = Literal["approved", "corrected"]
Status = Literal["pending", "resolved"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    flagged     TEXT NOT NULL,  -- JSON of Flagged
    resolved_at TEXT,
    verdict     TEXT            -- JSON of Verdict; NULL while pending
)
"""


class ReviewError(RuntimeError):
    """The queue couldn't be read or written."""


class ReviewNotFoundError(ReviewError):
    pass


class AlreadyResolvedError(ReviewError):
    pass


class Flagged(BaseModel):
    """What the engineer needs to judge an answer: a snapshot taken when it was flagged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    question: str
    mode: str
    answer: str
    citations: tuple[str, ...]  # chunk IDs, e.g. 'CD109-5.1'
    requirement_type: RequirementType
    is_departure: bool | None
    confidence: Confidence
    reasons: tuple[str, ...] = Field(min_length=1)  # why it was flagged
    trace_id: str | None = None


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    reviewer: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    # For `corrected`: what the right answer is. Becomes the golden answer, so it's required.
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)
    # The clauses a correct answer cites, if they differ from the answer's own. Chunk IDs.
    expected: tuple[str, ...] = ()

    @field_validator("reviewer", "note", mode="before")
    @classmethod
    def _trim(cls, raw: object) -> object:
        return raw.strip() if isinstance(raw, str) else raw

    @field_validator("expected", mode="before")
    @classmethod
    def _chunk_ids(cls, raw: object) -> object:
        if not isinstance(raw, list | tuple):
            return raw
        try:
            return tuple(dict.fromkeys(normalise_citation(str(r)) for r in raw))
        except CitationError as err:
            raise ValueError(str(err)) from err

    @model_validator(mode="after")
    def _correction_says_what_is_right(self) -> Self:
        if self.outcome == "corrected" and not self.note:
            raise ValueError("a corrected verdict needs a note saying what the right answer is")
        return self


class ReviewItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    created_at: datetime
    flagged: Flagged
    resolved_at: datetime | None = None
    verdict: Verdict | None = None

    @property
    def status(self) -> Status:
        return "pending" if self.verdict is None else "resolved"


class QueueStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    pending: int
    approved: int
    corrected: int


def _item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        flagged=Flagged.model_validate_json(row["flagged"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        verdict=Verdict.model_validate_json(row["verdict"]) if row["verdict"] else None,
    )


class ReviewQueue:
    def __init__(
        self, path: Path = REVIEW_DB, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._path, self._now = path, now

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One connection per operation, committed on success. Every sqlite or filesystem
        failure surfaces as ReviewError, so callers handle one exception type."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path, timeout=10)
        except (OSError, sqlite3.Error) as err:
            raise ReviewError(f"couldn't open the review queue ({self._path.name}): {err}") from err
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(_SCHEMA)
            with connection:  # commits, or rolls back if the block raises
                yield connection
        except sqlite3.Error as err:
            raise ReviewError(f"review queue ({self._path.name}): {err}") from err
        finally:
            connection.close()

    def add(self, flagged: Flagged) -> ReviewItem:
        created = self._now()
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO reviews (created_at, flagged) VALUES (?, ?)",
                (created.isoformat(), flagged.model_dump_json()),
            )
            return ReviewItem(id=cursor.lastrowid, created_at=created, flagged=flagged)

    def get(self, review_id: int) -> ReviewItem:
        with self._db() as db:
            row = db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise ReviewNotFoundError(f"no review {review_id}")
        return _item(row)

    def list(self, status: Status | None = None, limit: int = MAX_LISTED) -> list[ReviewItem]:
        """Pending: oldest first (the order to work through). Resolved: newest first.
        limit=-1 returns every row (SQLite reads a negative LIMIT as no limit)."""
        where = {
            "pending": "WHERE verdict IS NULL ORDER BY id",
            "resolved": "WHERE verdict IS NOT NULL ORDER BY resolved_at DESC, id DESC",
            None: "ORDER BY id",
        }[status]
        with self._db() as db:
            rows = db.execute(f"SELECT * FROM reviews {where} LIMIT ?", (limit,)).fetchall()  # noqa: S608 - `where` is one of three constants above
        return [_item(row) for row in rows]

    def resolve(self, review_id: int, verdict: Verdict) -> ReviewItem:
        """Record the verdict, once. A second verdict is refused, not overwritten: the first
        may already have been acted on or exported."""
        with self._db() as db:
            # The WHERE makes this atomic: of two reviewers submitting together, one wins.
            done = db.execute(
                "UPDATE reviews SET verdict = ?, resolved_at = ? WHERE id = ? AND verdict IS NULL",
                (verdict.model_dump_json(), self._now().isoformat(), review_id),
            )
            row = db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise ReviewNotFoundError(f"no review {review_id}")
        if done.rowcount == 0:
            raise AlreadyResolvedError(f"review {review_id} already has a verdict")
        return _item(row)

    def stats(self) -> QueueStats:
        # Counted in SQL: this runs on every GET /reviews, so it shouldn't parse every row.
        with self._db() as db:
            rows = db.execute(
                "SELECT COALESCE(json_extract(verdict, '$.outcome'), 'pending') AS state, "
                "COUNT(*) AS n FROM reviews GROUP BY state"
            ).fetchall()
        counts = {row["state"]: row["n"] for row in rows}
        return QueueStats(
            pending=counts.get("pending", 0),
            approved=counts.get("approved", 0),
            corrected=counts.get("corrected", 0),
        )
