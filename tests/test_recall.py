import pytest
import yaml

from evals.recall import (
    QuestionsError,
    RetrievalQuestion,
    evaluate,
    fetch_size,
    format_report,
    headline_mode,
    index_mismatch,
    load_questions,
    mean_coverage,
    mean_reciprocal_rank,
    misses,
    recall,
    score,
    unknown_expected,
)
from index.search import Hit, Mode
from tests.index_fixtures import vector


def _write(tmp_path, rows):
    path = tmp_path / "retrieval.yaml"
    path.write_text(rows if isinstance(rows, str) else yaml.safe_dump(rows, sort_keys=False))
    return path


def _q(qid="R01", expected=("CD 109 cl. 5.3.1",), query="max gradient?"):
    return RetrievalQuestion(id=qid, query=query, expected=list(expected))


def _hits(*citation_ids):
    return [Hit(c, c.split("#")[0], 1.0) for c in citation_ids]


def test_expected_citations_are_normalised_to_chunk_ids():
    q = _q(expected=("CD 109 cl. 5.3.1", "CD 109 Table 3.5", "CD 123 cl. E/1.1", "CD109-2.10"))

    assert q.expected == ("CD109-5.3.1", "CD109-Table-3.5", "CD123-E/1.1", "CD109-2.10")


def test_load_questions_reads_the_hand_written_yaml_format(tmp_path):
    # Exactly the layout the template shows Ben, comments and blank lines included.
    path = _write(
        tmp_path,
        """# questions
- id: R01
  query: "What is the maximum superelevation on an all-purpose road?"
  expected: [CD 109 cl. 4.5]

- id: R02
  query: "Layout constraint: what value for a D2AP?"
  expected:
    - CD 109 Table 2.3
    - CD 109 cl. 2.4
  notes: table lookup
""",
    )

    questions = load_questions(path)

    assert [q.id for q in questions] == ["R01", "R02"]
    assert questions[0].expected == ("CD109-4.5",)
    assert questions[1].expected == ("CD109-Table-2.3", "CD109-2.4")
    assert questions[1].query.startswith("Layout constraint:")
    assert questions[1].notes == "table lookup"


def test_load_questions_reports_every_bad_entry(tmp_path):
    path = _write(
        tmp_path,
        [
            {"id": "R01", "query": "q1", "expected": ["CD 109 cl. 4.5"]},
            {"id": "R02", "query": "q2", "expected": []},
            {"id": "R03", "query": "q3", "expected": ["clause 4.5"]},  # no document code
            {"id": "R01", "query": "dup", "expected": ["CD 109 cl. 4.6"]},
            {"id": "R05", "query": "q5", "expected": ["CD 109 cl. 1.1"], "answer": "typo field"},
            {"id": "R06", "query": "q6", "expected": [4.5]},  # bare number, no document
            "just a string",
        ],
    )

    with pytest.raises(QuestionsError) as err:
        load_questions(path)
    message = str(err.value)
    for fragment in (
        "question 2 (R02): expected",
        "question 3 (R03): expected",
        "question 4 (R01): duplicate id",
        "question 5 (R05): unknown field 'answer'",
        "question 6 (R06): expected",
        "question 7: ",
    ):
        assert fragment in message
    assert "question 1 " not in message


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("- id: R01\n  query: [unclosed\n", "line 3: expected"),
        ("id: R01\nquery: q\n", "must be a list"),
        ("# nothing yet\n", "no questions"),
    ],
)
def test_malformed_yaml_is_a_clear_error(tmp_path, text, match):
    with pytest.raises(QuestionsError, match=match):
        load_questions(_write(tmp_path, text))


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(QuestionsError, match="not found"):
        load_questions(tmp_path / "nope.yaml")


def test_unknown_expected_flags_citations_no_chunk_carries():
    questions = [
        _q("R01", ("CD 109 cl. 5.3.1",)),
        _q("R02", ("CD 109 cl. 5.3.1", "CD 109 cl. 9.9")),
    ]

    assert unknown_expected(questions, {"CD109-5.3.1"}) == {"R02": ("CD109-9.9",)}


def test_score_finds_rank_of_first_expected_clause_and_coverage():
    result = score(
        _q(expected=("CD 109 cl. 5.3.1", "CD 109 cl. 5.3.2")),
        _hits("CD109-1.1", "CD109-5.3.2", "CD109-2.1"),
        k=5,
    )

    assert result.rank == 2
    assert result.coverage == 0.5  # 5.3.2 found, 5.3.1 not


def test_split_parts_count_as_their_clause_and_once_only():
    # Two parts of 1.1 take ranks 1-2 in the raw hits, but 5.3.1 is the second distinct clause.
    result = score(_q(), _hits("CD109-1.1", "CD109-1.1#2", "CD109-5.3.1#3"), k=5)

    assert result.retrieved == ("CD109-1.1", "CD109-5.3.1")
    assert result.rank == 2


def test_split_parts_do_not_use_up_the_k_distinct_clauses():
    # Raw position 5, but only the third distinct clause: a hit at k=3.
    hits = _hits("CD109-1.1", "CD109-1.1#2", "CD109-2.1", "CD109-2.1#2", "CD109-5.3.1")

    assert score(_q(), hits, k=3).rank == 3
    assert score(_q(), hits, k=2).rank is None  # cut at k distinct clauses


def test_miss_has_no_rank_and_no_coverage():
    result = score(_q(), _hits("CD109-1.1"), k=5)

    assert result.rank is None and result.coverage == 0


def test_recall_mrr_and_coverage():
    two = ("CD 109 cl. 5.3.1", "CD 109 Table 5.1")
    results = [
        score(_q(), _hits("CD109-5.3.1"), k=5),
        score(_q(expected=two), _hits("x", "y", "CD109-5.3.1"), k=5),
        score(_q(), _hits(), k=5),
    ]

    assert recall(results) == pytest.approx(2 / 3)
    assert mean_reciprocal_rank(results) == pytest.approx((1 + 1 / 3) / 3)
    assert mean_coverage(results) == pytest.approx((1 + 0.5 + 0) / 3)


@pytest.mark.parametrize("metric", [recall, mean_reciprocal_rank, mean_coverage])
def test_metrics_of_nothing_are_an_error_not_zero(metric):
    with pytest.raises(ValueError):
        metric([])


def test_evaluate_fetches_a_wider_pool_than_k():
    questions = [_q("R01"), _q("R02", ("CD 109 Table 5.1",))]
    calls = []

    def fake_search(query, vec, mode, top):
        calls.append((query, mode, top))
        return _hits("CD109-5.3.1") if mode is Mode.SEMANTIC else _hits("CD109-1.1")

    results = evaluate(
        questions, [vector(), vector()], [Mode.KEYWORD, Mode.SEMANTIC], 5, fake_search
    )

    assert len(calls) == 4 and all(top == fetch_size(5) > 5 for _, _, top in calls)
    assert [r.rank for r in results[Mode.SEMANTIC]] == [1, None]
    assert [r.rank for r in results[Mode.KEYWORD]] == [None, None]


def test_fetch_size_grows_with_large_k():
    assert fetch_size(5) == 20 and fetch_size(15) == 30


def test_evaluate_rejects_mismatched_vectors():
    with pytest.raises(ValueError, match="2 questions but 1 vectors"):
        evaluate([_q("R01"), _q("R02")], [vector()], [Mode.HYBRID], 5, lambda *a: [])


def test_report_is_a_markdown_table_with_summary_rows():
    questions = [_q("R01"), _q("R02")]
    results = {
        Mode.HYBRID: [
            score(questions[0], _hits("CD109-5.3.1"), k=5),
            score(questions[1], _hits(), k=5),
        ],
        Mode.SEMANTIC: [
            score(questions[0], _hits("x", "CD109-5.3.1"), k=5),
            score(questions[1], _hits("CD109-5.3.1"), k=5),
        ],
    }

    report = format_report(questions, results, 5)

    assert report.splitlines()[0] == "| Question | Expected | hybrid | semantic |"
    assert "| R01 | CD109-5.3.1 | 1 | 2 |" in report
    assert "| R02 | CD109-5.3.1 | miss | 1 |" in report
    assert "| **recall@5** | | **50%** | **100%** |" in report
    assert "| MRR@5 | | 0.50 | 0.75 |" in report
    assert "| coverage@5 | | 50% | 100% |" in report


def test_misses_show_what_came_back_instead():
    questions = [_q("R01", query="gradient?")]
    lines = misses(questions, [score(questions[0], _hits("CD109-1.1"), k=5)])

    assert lines == ["R01 'gradient?': expected CD109-5.3.1; got CD109-1.1"]


def test_headline_mode_prefers_semantic_else_last_given():
    assert headline_mode(list(Mode)) is Mode.SEMANTIC
    assert headline_mode([Mode.KEYWORD, Mode.HYBRID]) is Mode.HYBRID


def test_index_mismatch_names_missing_and_stale_documents():
    assert index_mismatch({"a", "b"}, {"a", "b"}) is None
    problem = index_mismatch({"a", "b"}, {"a", "old"})

    assert "1 chunks not indexed, 1 stale" in problem and "index.rebuild" in problem


def test_blank_query_and_non_utf8_file_are_rejected(tmp_path):
    with pytest.raises(QuestionsError, match="query"):
        load_questions(_write(tmp_path, [{"id": "R01", "query": "  ", "expected": ["CD109-1.1"]}]))
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(QuestionsError, match="UTF-8"):
        load_questions(path)


def _error_for(tmp_path, text):
    with pytest.raises(QuestionsError) as err:
        load_questions(_write(tmp_path, text))
    return str(err.value)


def test_a_key_repeated_in_one_question_is_rejected_not_overwritten(tmp_path):
    # Copying a block and leaving the old line in place: safe_load would keep the last one.
    text = '- id: R01\n  query: "q"\n  expected: [CD 109 cl. 4.5]\n  expected: [CD 109 cl. 9.9]\n'

    message = _error_for(tmp_path, text)

    assert "line 4" in message and "'expected' appears twice" in message


def test_a_bare_clause_is_accepted_and_repeats_are_dropped(tmp_path):
    text = (
        '- id: R01\n  query: "q"\n  expected: CD 109 cl. 4.5\n'
        '- id: R02\n  query: "q"\n  expected: [CD 109 cl. 4.5, CD109-4.5]\n'
    )

    questions = load_questions(_write(tmp_path, text))

    assert [q.expected for q in questions] == [("CD109-4.5",), ("CD109-4.5",)]


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (
            '- id: no\n  query: "q"\n  expected: [CD 109 cl. 4.5]\n',
            "question 1: id must be text: put it in double quotes",
        ),
        ('- id: R01\n  query: "q"\n  expected:\n', "list at least one clause"),
        ('- id: R01\n  query: "q"\n  expectd: [CD 109 cl. 4.5]\n', "unknown field 'expectd'"),
        ("- id: R01\n  query: Layout: what value?\n  expected: [CD 109 cl. 4.5]\n", "line 2"),
        ('- id: R01\n\tquery: "q"\n', "a tab instead of spaces"),
        ('- id: EXAMPLE-1\n  query: "q"\n  expected: [CD 109 cl. 4.5]\n', "template's example"),
    ],
)
def test_hand_editing_mistakes_get_plain_messages(tmp_path, text, fragment):
    assert fragment in _error_for(tmp_path, text)
