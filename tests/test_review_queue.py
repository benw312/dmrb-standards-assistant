"""The review queue on a temp SQLite file: add, list, resolve once, and failure paths."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from review.queue import (
    AlreadyResolvedError,
    Flagged,
    ReviewError,
    ReviewNotFoundError,
    ReviewQueue,
    Verdict,
)


def flagged(**overrides) -> Flagged:
    fields = {
        "request_id": "abc123",
        "question": "Can I use a 5% gradient on a motorway?",
        "mode": "agent",
        "answer": "No: 4% is the limit [CD 109 cl. 5.1].",
        "citations": ("CD109-5.1",),
        "requirement_type": "shall",
        "is_departure": True,
        "confidence": "high",
        "reasons": ("possible Departure from Standard",),
        "trace_id": "20261113-000000-abc123",
    }
    return Flagged(**(fields | overrides))


APPROVED = Verdict(outcome="approved", reviewer="Ben Wallbridge")


@pytest.fixture
def queue(tmp_path) -> ReviewQueue:
    ticks = iter(datetime(2026, 11, 13, 9, tzinfo=UTC) + timedelta(minutes=m) for m in range(99))
    return ReviewQueue(tmp_path / "reviews.sqlite", now=lambda: next(ticks))


def test_an_added_answer_is_pending_and_reads_back_unchanged(queue):
    item = queue.add(flagged())

    assert item.status == "pending" and item.verdict is None
    assert queue.get(item.id) == item
    assert queue.get(item.id).flagged == flagged()


def test_the_queue_survives_a_new_process(tmp_path):
    first = ReviewQueue(tmp_path / "reviews.sqlite").add(flagged())
    assert ReviewQueue(tmp_path / "reviews.sqlite").get(first.id).flagged == flagged()


def test_pending_lists_oldest_first_and_resolved_newest_first(queue):
    a, b, c = (queue.add(flagged(request_id=r)) for r in "abc")
    queue.resolve(a.id, APPROVED)
    queue.resolve(c.id, APPROVED)

    assert [i.id for i in queue.list("pending")] == [b.id]
    assert [i.id for i in queue.list("resolved")] == [c.id, a.id]
    assert [i.id for i in queue.list()] == [a.id, b.id, c.id]
    assert [i.id for i in queue.list(limit=2)] == [a.id, b.id]


def test_a_verdict_is_recorded_with_who_and_when(queue):
    item = queue.add(flagged())
    verdict = Verdict(
        outcome="corrected",
        reviewer="  Ben Wallbridge ",
        note="8% is a relaxation for all-purpose roads, not motorways.",
        expected=["CD 109 cl. 5.1", "CD109-Table-5.1", "[CD 109 cl. 5.1]"],
    )

    done = queue.resolve(item.id, verdict)

    assert done.status == "resolved" and done.resolved_at > done.created_at
    assert done.verdict.reviewer == "Ben Wallbridge"
    assert done.verdict.expected == ("CD109-5.1", "CD109-Table-5.1")  # normalised, de-duplicated
    assert queue.get(item.id) == done


def test_a_second_verdict_is_refused_and_the_first_kept(queue):
    item = queue.add(flagged())
    queue.resolve(item.id, APPROVED)

    with pytest.raises(AlreadyResolvedError):
        queue.resolve(item.id, Verdict(outcome="corrected", reviewer="Someone", note="No."))
    assert queue.get(item.id).verdict == APPROVED


def test_only_one_of_two_simultaneous_verdicts_wins(queue):
    item = queue.add(flagged())
    outcomes = []

    def submit(name):
        try:
            queue.resolve(item.id, Verdict(outcome="approved", reviewer=name))
            outcomes.append("won")
        except AlreadyResolvedError:
            outcomes.append("refused")

    threads = [threading.Thread(target=submit, args=(n,)) for n in ("A", "B", "C", "D")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["refused", "refused", "refused", "won"]


def test_an_unknown_id_is_not_found(queue):
    with pytest.raises(ReviewNotFoundError):
        queue.get(99)
    with pytest.raises(ReviewNotFoundError):
        queue.resolve(99, APPROVED)


def test_stats_count_each_state(queue):
    a, b, _ = (queue.add(flagged(request_id=r)) for r in "abc")
    queue.resolve(a.id, APPROVED)
    queue.resolve(b.id, Verdict(outcome="corrected", reviewer="Ben", note="It's 4%."))

    stats = queue.stats()
    assert (stats.pending, stats.approved, stats.corrected) == (1, 1, 1)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"outcome": "corrected", "reviewer": "Ben"}, "needs a note"),
        ({"outcome": "corrected", "reviewer": "Ben", "note": "   "}, "needs a note"),
        ({"outcome": "approved", "reviewer": "  "}, "reviewer"),
        ({"outcome": "maybe", "reviewer": "Ben"}, "outcome"),
        ({"outcome": "approved", "reviewer": "Ben", "expected": ["clause five"]}, "expected"),
        ({"outcome": "approved", "reviewer": "Ben", "note": "x" * 2001}, "note"),
        ({"outcome": "approved", "reviewer": "Ben", "extra": 1}, "extra"),
    ],
)
def test_a_bad_verdict_is_rejected(fields, message):
    with pytest.raises(ValidationError, match=message):
        Verdict(**fields)


def test_a_flagged_answer_must_say_why():
    with pytest.raises(ValidationError, match="reasons"):
        flagged(reasons=())


def test_an_unusable_database_is_one_clear_error(tmp_path):
    not_a_db = tmp_path / "reviews.sqlite"
    not_a_db.write_text("this is not a SQLite file, " * 20)
    with pytest.raises(ReviewError, match="reviews.sqlite"):
        ReviewQueue(not_a_db).add(flagged())

    blocked = tmp_path / "file.txt"
    blocked.write_text("x")
    with pytest.raises(ReviewError, match="couldn't open"):
        ReviewQueue(blocked / "reviews.sqlite").list()
