"""Formatting, checking and appending golden questions, on temp files only."""

import pytest
import yaml

from evals.golden import GoldenError, GoldenQuestion, load_golden
from evals.golden_write import (
    append_question,
    counts,
    format_block,
    golden_path,
    load_or_empty,
    next_id,
    problems,
    search_chunks,
)
from ingest.models import Chunk

TEMPLATE = """\
# Golden set: ground truth, written by Ben.
# - id: EXAMPLE-lookup
#   query: "An example?"
#
# ------------------------------------------------ Ben's questions below
"""


def question(**changes) -> GoldenQuestion:
    fields = {
        "id": "G01",
        "query": "Can I use a 5% gradient on a motorway?",
        "type": "departure",
        "expected": ["CD 109 cl. 5.1", "CD 109 Table 5.1"],
        "requirement": "shall",
        "departure": True,
        "answer": "No: 4% is the limit, so a departure is needed.",
    }
    return GoldenQuestion.model_validate(fields | changes)


OUT_OF_SCOPE = GoldenQuestion(
    id="G02",
    query="What's the speed limit on a motorway?",
    type="out_of_scope",
    answer="Not covered: set by legislation.",
)


def chunk(
    citation_id="CD109-5.1", text="Gradients shall not exceed Table 5.1.", **changes
) -> Chunk:
    fields = {
        "id": citation_id,
        "citation_id": citation_id,
        "doc_code": "CD 109",
        "doc_title": "Highway link design",
        "version": "Revision 1",
        "label": citation_id.split("-", 1)[1],
        "kind": "clause",
        "requirement_type": "shall",
        "heading_path": "5. Vertical alignment > Gradients",
        "page": 25,
        "text": text,
        "url": "https://example.org/cd109#page=25",
    }
    return Chunk(**(fields | changes))


@pytest.fixture
def golden(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(TEMPLATE, encoding="utf-8")
    return path


def test_a_block_is_laid_out_like_the_files_examples():
    assert format_block(question(notes="From the review.")) == (
        "- id: G01\n"
        '  query: "Can I use a 5% gradient on a motorway?"\n'
        "  type: departure\n"
        "  expected: [CD 109 cl. 5.1, CD 109 Table 5.1]\n"
        "  requirement: shall\n"
        "  departure: true\n"
        '  answer: "No: 4% is the limit, so a departure is needed."\n'
        '  notes: "From the review."\n'
    )


def test_an_out_of_scope_block_leaves_out_what_doesnt_apply():
    block = format_block(OUT_OF_SCOPE)
    assert "expected" not in block and "requirement" not in block and "departure" not in block


def test_appending_keeps_every_comment_and_earlier_question(golden):
    append_question(golden, question())
    append_question(golden, OUT_OF_SCOPE)

    text = golden.read_text()
    assert text.startswith(TEMPLATE)  # nothing above the new blocks was rewritten
    assert load_golden(golden) == [question(), OUT_OF_SCOPE]
    assert not list(golden.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "awkward",
    [
        'He said "no": #5 [maybe] {x}',
        "line one\nline two",
        "50% - yes: no",
        "curly “quotes” and a tab\there",
        "nel\x85 and del\x7f",
        "  padded  ",
    ],
)
def test_awkward_text_reads_back_exactly(golden, awkward):
    q = question(query=f"Is this ok? {awkward}".strip(), answer=awkward, notes=awkward)
    append_question(golden, q)
    assert load_golden(golden) == [q]


def test_clauses_from_annexes_tables_figures_and_glossaries_read_back(golden):
    q = question(
        expected=["CD 123 cl. E/1.1", "CD 109 Table 2.10", "CG 501 Figure 2.5", "CD 109 Glossary"]
    )
    append_question(golden, q)
    assert load_golden(golden)[0].expected == q.expected


def test_a_broken_file_is_refused_and_left_exactly_as_it_was(golden):
    broken = TEMPLATE + "- id: G01\n  query: no closing quote\n  type: nonsense\n"
    golden.write_text(broken)
    with pytest.raises(GoldenError, match="type"):
        append_question(golden, OUT_OF_SCOPE)
    assert golden.read_text() == broken and not list(golden.parent.glob("*.tmp"))


def test_a_duplicate_is_refused_by_the_loader_and_nothing_changes(golden):
    append_question(golden, question())
    saved = golden.read_text()
    with pytest.raises(GoldenError, match="duplicate id"):
        append_question(golden, question(query="A different question?"))
    assert golden.read_text() == saved


def test_a_missing_file_is_an_error_not_a_new_file(tmp_path):
    with pytest.raises(GoldenError, match="not found"):
        append_question(tmp_path / "golden.yaml", question())
    assert not (tmp_path / "golden.yaml").exists()


def test_load_or_empty_reads_a_template_only_file_as_no_questions(golden):
    assert load_or_empty(golden) == []
    append_question(golden, question())
    assert load_or_empty(golden) == [question()]


def test_next_id_follows_the_highest_number_used():
    assert next_id([]) == "G01"
    assert next_id([question(id="G01"), question(id="g07"), question(id="R003")]) == "G08"
    assert next_id([question(id=f"G{n}") for n in (98, 99)]) == "G100"


def test_problems_are_named_in_plain_words():
    existing = [question()]
    ids = {"CD109-5.1"}

    assert problems(OUT_OF_SCOPE, existing, ids) == []
    assert (
        problems(question(id="g01", query="Another?"), existing, ids)[0] == "id g01 is already used"
    )
    again = problems(
        question(id="G09", query="can i use a 5% GRADIENT on a motorway?"), existing, ids
    )
    assert "G01 already asks this question" in again
    assert "not in the indexed standards: CD 109 Table 5.1" in again
    assert problems(question(id="G09", query="Q?", answer="  "), [], None) == [
        "answer: say what a good answer says, in a sentence or two"
    ]


def test_without_local_chunks_the_clause_check_is_skipped():
    assert problems(question(expected=["CD 109 cl. 99.9"]), [], None) == []


def test_counts_list_every_type_including_the_empty_ones():
    tally = counts([question(), OUT_OF_SCOPE, question(id="G03", query="Other?")])
    assert tally == {
        "lookup": 0,
        "multi_hop": 0,
        "departure": 2,
        "calculation": 0,
        "out_of_scope": 1,
    }


CHUNKS = [
    chunk("CD109-5.1", "Longitudinal gradients on motorways shall not exceed Table 5.1."),
    chunk("CD109-5.1.1", "Desirable maximum gradients should be used.", requirement_type="should"),
    chunk(
        "CD109-5.10", "Unrelated clause about crest curves.", heading_path="5. Vertical > Crests"
    ),
    chunk("CD109-Table-5.1", "| Motorway | 3% | 4% |", kind="table", requirement_type=None),
    chunk(
        "CD109-4.2",
        "Superelevation shall follow Equation 4.2; gradient gradient gradient.",
        heading_path="4. Horizontal alignment > Superelevation",
    ),
    chunk("CD109-4.2#2", "More superelevation text.", heading_path="4. Horizontal alignment"),
]
CHUNKS[-1] = CHUNKS[-1].model_copy(update={"citation_id": "CD109-4.2"})


def _found(text: str) -> list[str]:
    return [c.citation_id for c in search_chunks(CHUNKS, text)]


def test_a_citation_finds_the_clause_then_its_sub_clauses_but_not_lookalikes():
    assert _found("CD 109 cl. 5.1") == ["CD109-5.1", "CD109-5.1.1"]  # not 5.10
    assert _found("[CD 109 Table 5.1]") == ["CD109-Table-5.1"]
    assert _found("CD109-9.9") == []


def test_words_must_all_appear_and_heading_matches_rank_first():
    # The table sits under the "Gradients" heading too; 4.2 only mentions the word.
    assert _found("gradient") == ["CD109-5.1", "CD109-5.1.1", "CD109-Table-5.1", "CD109-4.2"]
    assert _found("MOTORWAY gradients") == ["CD109-5.1", "CD109-Table-5.1"]
    assert _found("gradient unicorn") == [] and _found("   ") == []


def test_a_split_clause_shows_once_as_its_first_part():
    hits = search_chunks(CHUNKS, "superelevation")
    assert [c.id for c in hits] == ["CD109-4.2"]


def test_search_is_limited():
    many = [chunk(f"CD109-7.{n}", "gradient") for n in range(1, 30)]
    assert len(search_chunks(many, "gradient")) == 10
    assert len(search_chunks(many, "gradient", limit=3)) == 3


def test_the_file_edited_can_be_moved_with_an_environment_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("GOLDEN_FILE", raising=False)
    assert golden_path().name == "golden.yaml" and golden_path().parent.name == "evals"
    monkeypatch.setenv("GOLDEN_FILE", str(tmp_path / "scratch.yaml"))
    assert golden_path() == tmp_path / "scratch.yaml"


def test_every_saved_block_is_plain_yaml(golden):
    append_question(golden, question())
    (row,) = yaml.safe_load(golden.read_text())
    assert row["expected"] == ["CD 109 cl. 5.1", "CD 109 Table 5.1"] and row["departure"] is True


def test_a_glossary_ranks_last_and_repetition_doesnt_outrank_a_heading_match():
    glossary = chunk("CD109-Glossary", "gradient " * 50, kind="glossary", heading_path="Terms")
    wordy = chunk("CD109-8.7", "gradient " * 50, heading_path="8. Climbing lanes")
    hits = search_chunks([glossary, wordy, CHUNKS[1]], "gradient")
    assert [c.citation_id for c in hits] == ["CD109-5.1.1", "CD109-8.7", "CD109-Glossary"]
