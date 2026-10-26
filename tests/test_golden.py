"""Golden-set loader and runner, with fake answers. No Azure."""

from pathlib import Path

import pytest

from evals.golden import (
    GOLDEN_PATH,
    GoldenError,
    JsonlSink,
    evaluate,
    load_golden,
    unknown_expected,
)
from tests.test_scoring import GRADIENT, _result


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "golden.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_template_has_only_commented_examples():
    with pytest.raises(GoldenError, match="no questions yet"):
        load_golden(GOLDEN_PATH)


def test_the_templates_examples_are_valid_once_uncommented(tmp_path):
    lines = GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# - id: EXAMPLE"))
    body = [
        line.removeprefix("# ").removeprefix("#") for line in lines[start:] if "----" not in line
    ]
    text = "\n".join(body).replace("EXAMPLE-", "G-")
    assert [q.type for q in load_golden(_write(tmp_path, text))] == [
        "lookup",
        "departure",
        "out_of_scope",
    ]


def test_a_valid_file_loads_with_citations_normalised(tmp_path):
    path = _write(
        tmp_path,
        """
- id: G01
  query: " Can I use a 5% gradient on a motorway? "
  type: departure
  expected: [CD 109 cl. 5.1, CD 109 Table 5.1]
  requirement: shall
  departure: true
- id: G02
  query: "Speed limit on a motorway?"
  type: out_of_scope
""",
    )
    first, second = load_golden(path)
    assert first.expected == ("CD109-5.1", "CD109-Table-5.1") and first.departure is True
    assert first.query == "Can I use a 5% gradient on a motorway?"
    assert second.expected == () and second.requirement is None


_OK = "\n  expected: [CD 109 cl. 5.1]"


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ('- id: EXAMPLE-1\n  query: "q"\n  type: lookup' + _OK, "example"),
        ('- id: G1\n  query: "   "\n  type: lookup' + _OK, "blank"),
        ('- id: G1\n  query: "q"\n  type: lookup', "at least one clause"),
        ('- id: G1\n  query: "q"\n  type: out_of_scope' + _OK, "out_of_scope"),
        (
            '- id: G1\n  query: "q"\n  type: out_of_scope\n  requirement: not_covered',
            "out_of_scope",
        ),
        ('- id: G1\n  query: "q"\n  type: out_of_scope\n  departure: false', "out_of_scope"),
        ('- id: G1\n  query: "q"\n  type: lookup\n  requirement: not_covered' + _OK, "only for"),
        ('- id: G1\n  query: "q"\n  type: departure' + _OK, "departure: true or false"),
        ('- id: G1\n  query: "q"\n  type: guess' + _OK, "type"),
        ('- id: G1\n  query: "q"\n  type: lookup\n  expected: [5.8]', "not a citation"),
        ('- id: G1\n  query: "q"\n  type: lookup\n  expectd: [CD 109 cl. 5.1]', "unknown field"),
    ],
)
def test_bad_rows_are_reported_readably(tmp_path, row, message):
    with pytest.raises(GoldenError, match=message):
        load_golden(_write(tmp_path, row))


def test_duplicate_ids_and_queries_are_rejected(tmp_path):
    row = '- id: G1\n  query: "Speed limit?"\n  type: out_of_scope\n'
    with pytest.raises(GoldenError, match="duplicate id"):
        load_golden(_write(tmp_path, row + row))
    other = row.replace("G1", "G2").replace("Speed", "speed")
    with pytest.raises(GoldenError, match="same query as G1"):
        load_golden(_write(tmp_path, row + other))


def test_unknown_expected_clauses_are_listed():
    assert unknown_expected([GRADIENT], {"CD109-5.1"}) == ["G01: CD109-Table-5.1"]
    assert unknown_expected([GRADIENT], {"CD109-5.1", "CD109-Table-5.1"}) == []


def test_evaluate_answers_each_question_and_records_it(tmp_path):
    asked = []

    def answerer(query, mode):
        asked.append((query, mode))
        return _result(("CD109-5.1", "CD109-Table-5.1"), departure=True)

    sink = JsonlSink(tmp_path / "results" / "run.jsonl")
    scores, failures = evaluate([GRADIENT], answerer, "graph", sink)
    assert asked == [("5% on a motorway?", "graph")] and failures == 0
    assert scores[0].mode == "graph" and scores[0].departure_ok
    assert '"citation_recall": 1.0' in sink.path.read_text()


def test_a_failing_question_is_counted_and_the_run_continues(tmp_path):
    calls = iter([RuntimeError("search down"), None])

    def answerer(query, mode):
        error = next(calls)
        if error:
            raise error
        return _result(("CD109-5.1",))

    sink = JsonlSink(tmp_path / "run.jsonl")
    scores, failures = evaluate([GRADIENT, GRADIENT], answerer, "rag", sink)
    assert failures == 1 and len(scores) == 1
    assert "RuntimeError: search down" in sink.path.read_text()
