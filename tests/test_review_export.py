"""Verdicts as golden-set candidates: an approved one must load as a real golden question."""

import yaml

from evals.golden import GoldenQuestion
from review.export import candidate, candidates, main
from review.queue import ReviewQueue, Verdict
from tests.test_review_queue import APPROVED, flagged


def _resolved(tmp_path, verdict: Verdict, **overrides):
    queue = ReviewQueue(tmp_path / "reviews.sqlite")
    return queue.resolve(queue.add(flagged(**overrides)).id, verdict), queue


def _load(text: str) -> dict:
    (row,) = yaml.safe_load(text)
    return row


def test_an_approved_answer_becomes_a_loadable_golden_question(tmp_path):
    item, _ = _resolved(tmp_path, APPROVED)

    question = GoldenQuestion.model_validate(_load(candidate(item)))

    assert question.id == "R001" and question.type == "departure"
    assert question.query == "Can I use a 5% gradient on a motorway?"
    assert question.expected == ("CD109-5.1",)
    assert question.requirement == "shall" and question.departure is True
    assert question.answer == "No: 4% is the limit [CD 109 cl. 5.1]."


def test_an_approved_refusal_is_an_out_of_scope_question(tmp_path):
    item, _ = _resolved(
        tmp_path,
        APPROVED,
        question="What's the speed limit on a motorway?",
        answer="Not covered by the indexed standards.",
        citations=(),
        requirement_type="not_covered",
        is_departure=None,
        reasons=("low confidence",),
    )
    question = GoldenQuestion.model_validate(_load(candidate(item)))
    assert question.type == "out_of_scope" and question.expected == ()


def test_a_correction_uses_the_engineers_words_not_the_wrong_answers_flags(tmp_path):
    verdict = Verdict(
        outcome="corrected",
        reviewer="Ben Wallbridge",
        note='4% is the limit: "shall".\nA 5% gradient needs a departure.',
        expected=["CD 109 cl. 5.1", "CD 109 Table 5.1"],
    )
    item, _ = _resolved(tmp_path, verdict, requirement_type="may", is_departure=False)

    text = candidate(item)
    row = _load(text)

    assert row["answer"] == verdict.note  # quotes and the line break survive
    assert row["expected"] == ["CD 109 cl. 5.1", "CD 109 Table 5.1"]
    assert "requirement" not in row and "departure" not in row  # not copied from a wrong answer
    assert "# TODO: requirement" in text and "Model answered (wrong)" in row["notes"]


def test_a_correction_without_clauses_leaves_a_todo(tmp_path):
    verdict = Verdict(outcome="corrected", reviewer="Ben", note="It's 4%.")
    item, _ = _resolved(tmp_path, verdict)
    text = candidate(item)
    assert "expected: []  # TODO" in text and _load(text)["expected"] == []


def test_an_approval_note_becomes_the_answer_and_the_engineers_clauses_win(tmp_path):
    verdict = Verdict(
        outcome="approved", reviewer="Ben", note="4% (shall).", expected=["CD 109 Table 5.1"]
    )
    item, _ = _resolved(tmp_path, verdict)
    row = _load(candidate(item))
    assert row["answer"] == "4% (shall)." and row["expected"] == ["CD 109 Table 5.1"]
    assert row["notes"].startswith("Model answer approved: No: 4%")


def test_the_header_names_who_reviewed_and_why_it_was_flagged(tmp_path):
    item, _ = _resolved(tmp_path, APPROVED)
    first = candidate(item).splitlines()[0]
    assert "approved by Ben Wallbridge" in first and "possible Departure" in first


def test_candidates_skip_pending_reviews_and_parse_as_one_list(tmp_path):
    item, queue = _resolved(tmp_path, APPROVED)
    queue.add(flagged(request_id="pending"))
    second = queue.resolve(queue.add(flagged(request_id="b")).id, APPROVED)

    rows = yaml.safe_load(candidates(queue.list()))

    assert [r["id"] for r in rows] == ["R001", f"R{second.id:03d}"]
    assert candidates([]) == ""


def test_main_prints_candidates_or_says_there_are_none(tmp_path, capsys):
    queue = ReviewQueue(tmp_path / "reviews.sqlite")
    assert main([], queue) == 0
    assert "No verdicts yet" in capsys.readouterr().out

    queue.resolve(queue.add(flagged()).id, APPROVED)
    assert main([], queue) == 0
    assert yaml.safe_load(capsys.readouterr().out)[0]["id"] == "R001"


def test_main_reports_an_unreadable_queue(tmp_path, capsys):
    path = tmp_path / "reviews.sqlite"
    path.write_text("not a database " * 30)
    assert main([], ReviewQueue(path)) == 1
    assert "error:" in capsys.readouterr().err
