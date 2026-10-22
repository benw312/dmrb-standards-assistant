import pytest

from agent.answer import Answer
from agent.citations import (
    CitationError,
    chunk_id,
    inline_citations,
    normalise_citation,
    verify_citations,
)

RETRIEVED = {"CD109-5.3.1", "CD109-5.3.2", "CD123-3.4"}


def make_answer(text: str, citations: list[str], requirement_type: str = "shall") -> Answer:
    return Answer(
        answer=text,
        citations=citations,
        requirement_type=requirement_type,
        is_departure=None,
        confidence="high",
        needs_human_review=False,
    )


# --- ID helpers ---------------------------------------------------------------


def test_chunk_id_joins_doc_code_and_clause():
    assert chunk_id("CD 109", "5.3.1") == "CD109-5.3.1"


@pytest.mark.parametrize(
    "raw",
    ["CD109-5.3.1", "CD 109 cl. 5.3.1", "[CD 109 cl. 5.3.1]", "CD 109 cl 5.3.1", " cd109-5.3.1 "],
)
def test_normalise_accepts_the_forms_a_model_produces(raw):
    assert normalise_citation(raw) == "CD109-5.3.1"


@pytest.mark.parametrize("raw", ["5.3.1", "CD 109", "Table 5.3", ""])
def test_normalise_rejects_citations_without_doc_and_clause(raw):
    with pytest.raises(CitationError):
        normalise_citation(raw)


def test_inline_citations_found_in_order_without_duplicates():
    text = (
        "Superelevation shall not exceed 7% [CD 109 cl. 5.3.1]. On bends see "
        "[CD 109 cl. 5.3.2; CD 123 cl. 3.4]. As above [CD 109 cl. 5.3.1]."
    )

    assert inline_citations(text) == ("CD109-5.3.1", "CD109-5.3.2", "CD123-3.4")


def test_bracketed_citation_at_sentence_end():
    assert inline_citations("See [CD 109 cl. 5.3].") == ("CD109-5.3",)


def test_unbracketed_citation_is_not_counted_as_cited():
    # The rigid form is the contract; loose text is flagged by verify_citations instead.
    assert inline_citations("See CD 109 cl. 5.3.") == ()


# --- verify_citations ---------------------------------------------------------


def test_grounded_answer_passes():
    answer = make_answer("Max 7% [CD 109 cl. 5.3.1].", ["CD109-5.3.1"])

    check = verify_citations(answer, RETRIEVED)

    assert check.ok
    assert check.reasons == ()


def test_citation_to_unretrieved_clause_is_a_hallucination():
    answer = make_answer("Max 7% [CD 109 cl. 9.9].", ["CD109-9.9"])

    check = verify_citations(answer, RETRIEVED)

    assert not check.ok
    assert check.not_retrieved == ("CD109-9.9",)
    assert "not among the retrieved clauses: CD109-9.9" in check.reasons[0]


def test_inline_citation_missing_from_list_is_caught():
    # The text relies on 5.3.2 but the structured list hides it from the gate.
    answer = make_answer("Max 7% [CD 109 cl. 5.3.1], see [CD 109 cl. 5.3.2].", ["CD109-5.3.1"])

    check = verify_citations(answer, RETRIEVED)

    assert not check.ok
    assert check.missing_from_list == ("CD109-5.3.2",)


def test_listed_citation_not_cited_in_text_is_caught():
    answer = make_answer("Max 7% [CD 109 cl. 5.3.1].", ["CD109-5.3.1", "CD123-3.4"])

    check = verify_citations(answer, RETRIEVED)

    assert not check.ok
    assert check.missing_from_text == ("CD123-3.4",)


def test_covered_answer_without_any_citation_fails():
    answer = make_answer("Max 7%.", [])

    check = verify_citations(answer, RETRIEVED)

    assert not check.ok
    assert "no citations" in check.reasons[0]


def test_unparseable_listed_citation_fails():
    answer = make_answer("Max 7% [CD 109 cl. 5.3.1].", ["CD109-5.3.1", "Table 5.3"])

    check = verify_citations(answer, RETRIEVED)

    assert not check.ok
    assert check.invalid == ("Table 5.3",)


def test_not_covered_refusal_needs_no_citations():
    answer = make_answer(
        "Not covered by the indexed standards; try the Traffic Signs Manual.",
        [],
        requirement_type="not_covered",
    )

    assert verify_citations(answer, RETRIEVED).ok


def test_not_covered_refusal_still_cannot_cite_unretrieved_clauses():
    answer = make_answer("Not covered [CD 999 cl. 1.1].", ["CD999-1.1"], "not_covered")

    assert not verify_citations(answer, RETRIEVED).ok


def test_reasons_are_readable_for_a_retry_prompt():
    answer = make_answer("Max 7% [CD 109 cl. 9.9].", ["CD109-9.9"])

    check = verify_citations(answer, RETRIEVED)

    assert all(isinstance(r, str) and r for r in check.reasons)


# --- Regression cases from code review ----------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[CD 109 cl. 5.3.1, 5.3.2]", ("CD109-5.3.1", "CD109-5.3.2")),
        ("[CD 109 cl. 5.3.1 and cl. 5.3.2]", ("CD109-5.3.1", "CD109-5.3.2")),
        ("[CD 109 cls. 5.3.1 & 5.3.2]", ("CD109-5.3.1", "CD109-5.3.2")),
        ("[CD 109 cl. 5.3.1; CD 123 cl. 3.4]", ("CD109-5.3.1", "CD123-3.4")),
        ("[CD 109 cl. 5.3.1; 5.3.2]", ("CD109-5.3.1", "CD109-5.3.2")),  # doc carries over
        ("[CD 109, cl. 5.3.1]", ("CD109-5.3.1",)),
        ("[CD 109 clause 5.3.1]", ("CD109-5.3.1",)),
        ("[CD 109 cl. 5.3.1a]", ("CD109-5.3.1a",)),
        ("[CD 109 cl. 05.3.1]", ("CD109-5.3.1",)),
    ],
)
def test_every_clause_in_a_bracket_group_is_read(text, expected):
    assert inline_citations(text) == expected


def test_second_clause_in_a_list_is_verified():
    answer = make_answer("Thick [CD 109 cl. 5.3.1, 5.9.9].", ["CD109-5.3.1", "CD109-5.9.9"])

    check = verify_citations(answer, RETRIEVED)

    assert check.not_retrieved == ("CD109-5.9.9",)


@pytest.mark.parametrize(
    "text",
    [
        "Max 7% [CD 109 cl. 5.3.1]. Also clause 9.9.9 of CD 109.",
        "Max 7% [CD 109 cl. 5.3.1], see CD 109 para 9.9.9.",
        "Max 7% [CD 109 cl. 5.3.1], per Table 9.9.",
        "Max 7% [CD 109 cl. 5.3.1], and CD 109 5.9.9 agrees.",
    ],
)
def test_unbracketed_references_fail_rather_than_slip_through(text):
    check = verify_citations(make_answer(text, ["CD109-5.3.1"]), RETRIEVED)

    assert not check.ok
    assert check.unbracketed


@pytest.mark.parametrize("group", ["[CD 109 Table 9.9]", "[CD 109 Figure 2]", "[CD 109]"])
def test_unreadable_bracket_groups_fail(group):
    check = verify_citations(make_answer(f"Max 7% {group}.", ["CD109-5.3.1"]), RETRIEVED)

    assert check.malformed == (group,)


def test_quoted_dmrb_wording_may_mention_tables():
    text = 'CD 109 says "superelevation shall be in accordance with Table 5.3" [CD 109 cl. 5.3.1].'

    assert verify_citations(make_answer(text, ["CD109-5.3.1"]), RETRIEVED).ok


def test_brackets_without_numbers_are_ignored():
    text = "The road [sic] shall be 7% max [CD 109 cl. 5.3.1]."

    assert verify_citations(make_answer(text, ["CD109-5.3.1"]), RETRIEVED).ok


def test_refusal_may_not_cite_clauses():
    answer = make_answer("Not covered [CD 109 cl. 5.3.1].", ["CD109-5.3.1"], "not_covered")

    check = verify_citations(answer, RETRIEVED)

    assert check.refusal_cites
    assert "must not cite" in check.reasons[0]


def test_refusal_may_name_a_standard_to_try():
    answer = make_answer("Not covered by the indexed standards; try CD 116.", [], "not_covered")

    assert verify_citations(answer, RETRIEVED).ok


def test_lettered_subclause_round_trips():
    answer = make_answer("Use 3 m [CD 109 cl. 5.3.1a].", ["CD109-5.3.1A"])

    assert verify_citations(answer, {"CD109-5.3.1a"}).ok


def test_lettered_subclause_does_not_support_its_parent():
    answer = make_answer("Use 3 m [CD 109 cl. 5.3.1a].", ["CD109-5.3.1a"])

    assert verify_citations(answer, {"CD109-5.3.1"}).not_retrieved == ("CD109-5.3.1a",)


def test_malformed_retrieved_id_is_logged_not_raised(caplog):
    answer = make_answer("Max 7% [CD 109 cl. 5.3.1].", ["CD109-5.3.1"])

    check = verify_citations(answer, {"CD109-5.3.1", "garbage"})

    assert check.ok
    assert "garbage" in caplog.text


@pytest.mark.parametrize("raw", ["CD 109 - 5.3.1", "CD109 -5.3.1"])
def test_normalise_tolerates_spaced_hyphen(raw):
    assert normalise_citation(raw) == "CD109-5.3.1"


def test_normalise_rejects_multiple_clauses():
    with pytest.raises(CitationError, match="exactly one"):
        normalise_citation("CD 109 cl. 5.3.1, 5.3.2")


def test_lowercase_prose_is_not_mistaken_for_a_doc_code():
    text = "Max 7% [CD 109 cl. 5.3.1]; at km 150 3.5 m lanes apply."

    assert verify_citations(make_answer(text, ["CD109-5.3.1"]), RETRIEVED).ok


# --- GG 101 conventions ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[CD 109 cl. E/1.1]", "CD109-E/1.1"),
        ("CD109-E/1.1", "CD109-E/1.1"),
        ("cd109-e/1.1", "CD109-E/1.1"),
        ("GG 101 cl. NI/1.2", "GG101-NI/1.2"),
    ],
)
def test_national_annex_clauses_are_citable(raw, expected):
    assert normalise_citation(raw) == expected


def test_annex_clause_is_distinct_from_core_clause():
    answer = make_answer("Inform the OO [CD 109 cl. E/1.1].", ["CD109-E/1.1"])

    assert verify_citations(answer, {"CD109-1.1"}).not_retrieved == ("CD109-E/1.1",)


def test_unbracketed_annex_reference_is_flagged():
    text = "Max 7% [CD 109 cl. 5.3.1]; see also clause E/1.1."

    assert verify_citations(make_answer(text, ["CD109-5.3.1"]), RETRIEVED).unbracketed


@pytest.mark.parametrize("raw", ["XD 109 cl. 1.1", "CX109-1.1"])
def test_doc_codes_outside_gg101_scheme_are_not_citations(raw):
    with pytest.raises(CitationError):
        normalise_citation(raw)


def test_lowercase_annex_prefix_in_brackets_is_read():
    assert inline_citations("[CD 109 cl. e/1.1]") == ("CD109-E/1.1",)


def test_stray_full_stop_inside_brackets_is_tolerated():
    assert inline_citations("[CD 109 cl. 5.3.1.]") == ("CD109-5.3.1",)


@pytest.mark.parametrize(
    ("text", "reported"),
    [("See CD 109 cl. E/1.1 too.", "cl. E/1.1"), ("Per Table E/1.1 here.", "Table E/1.1")],
)
def test_unbracketed_reference_is_reported_in_full(text, reported):
    check = verify_citations(make_answer(f"Max [CD 109 cl. 5.3.1]. {text}", ["CD109-5.3.1"]), {})

    assert reported in check.unbracketed
