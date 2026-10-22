"""Tests for the GG 101 drafting conventions.

Quoted DMRB text: GG 101 Version 0.1.0 and CD 109 Revision 1, © Crown copyright,
reused under the Open Government Licence v3.0.
"""

import pytest

from ingest.conventions import (
    VERBAL_FORMS,
    ClauseKind,
    ConventionError,
    classify_verbal_form,
    clause_consistency_warning,
    note_number,
    parse_clause_number,
    parse_doc_code,
    parse_footer,
)

# --- Verbal forms (GG 101 Introduction) ----------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Statutory and legislative requirements must always be followed.", "must"),
        (
            "The requirements in this document shall be used in conjunction with all "
            "appropriate DMRB documents.",
            "shall",
        ),
        (
            "Bulk departure applications should be submitted in preference to a number of "
            "individual departures.",
            "should",
        ),
        (
            "Requirements may be applied to other roads with the approval of the specific "
            "highway or local authority acting as the Overseeing Organisation.",
            "may",
        ),
        ("Departures are not applicable to statutory and legislative requirements.", "note"),
        ("Overseeing Organisation land can be near to the motorway.", "note"),
    ],
)
def test_classifies_gg101_sentences(text, expected):
    assert classify_verbal_form(text) == expected


def test_strongest_verb_wins_in_a_mixed_clause():
    text = "The layout shall be checked; a tighter radius may be used where approved."

    assert classify_verbal_form(text) == "shall"


def test_negated_verbs_keep_their_strength():
    assert classify_verbal_form("GG 101, clause 1.2 shall not apply in Northern Ireland.") == (
        "shall"
    )


def test_month_may_is_not_a_permission():
    assert classify_verbal_form("This edition was published in May 2020.") == "note"


def test_verbs_inside_words_do_not_count():
    assert classify_verbal_form("Mayfield Road has a shallow embankment.") == "note"


def test_only_shall_can_be_varied_by_departure():
    # GG 101 2.3 (must: never), 2.4 (shall: departure), 2.12 (should: justification).
    departures = {verb for verb, form in VERBAL_FORMS.items() if form.departure_applies}

    assert departures == {"shall"}


def test_each_verbal_form_explains_how_it_can_be_varied():
    assert "safety risk assessment" in VERBAL_FORMS["should"].variation
    assert "cannot be varied" in VERBAL_FORMS["must"].variation
    assert all(form.source.startswith("GG 101") for form in VERBAL_FORMS.values())


# --- Clause numbering (GG 101 Appendix B) --------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "naa", "parent"),
    [
        ("6", ClauseKind.SECTION, None, None),
        ("6.1", ClauseKind.REQUIREMENT, None, None),
        ("6.1.1", ClauseKind.ADVICE, None, "6.1"),
        ("6.1.1.", ClauseKind.ADVICE, None, "6.1"),  # GG 101 Table B.1 prints "6.1.1."
        ("E/1.1", ClauseKind.REQUIREMENT, "E", None),
        ("NI/1.2", ClauseKind.REQUIREMENT, "NI", None),
        ("E/2.4.1", ClauseKind.ADVICE, "E", "E/2.4"),
        ("E/1", ClauseKind.SECTION, "E", None),
    ],
)
def test_numbering_level_gives_clause_kind(raw, kind, naa, parent):
    number = parse_clause_number(raw)

    assert number.kind is kind
    assert number.naa == naa
    assert number.parent == parent


def test_clause_number_round_trips_to_text():
    assert str(parse_clause_number("E/2.4.1")) == "E/2.4.1"


@pytest.mark.parametrize("raw", ["", "1.2.3.4", "X/1.1", "1.a", "Table 2.1", "1..2"])
def test_rejects_numbers_outside_the_scheme(raw):
    with pytest.raises(ConventionError):
        parse_clause_number(raw)


@pytest.mark.parametrize(
    ("number", "verb", "warns"),
    [
        ("2.3", "must", False),
        ("2.4", "shall", False),
        ("2.4.1", "should", False),
        ("1.2.1", "may", False),
        ("2.4", "should", True),  # requirement worded as advice
        ("2.4.1", "shall", True),  # advice worded as a requirement
        ("2.4", "note", True),  # numbered requirement with no requirement verb
        ("2.4.1", "note", True),
    ],
)
def test_flags_clauses_whose_verb_contradicts_their_number(number, verb, warns):
    warning = clause_consistency_warning(parse_clause_number(number), verb)

    assert (warning is not None) is warns


# --- Notes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("NOTE Departures are not applicable to statutory requirements.", 0),
        ("NOTE: The statement of intent is typically a brief document.", 0),
        ("NOTE 2 Overseeing Organisation assets remote from the network.", 2),
        ("NOTE 1: Where any document within the DMRB refers to...", 1),
        ("Notes provide information assisting users.", None),
        ("6.1 The format of the statement of intent shall be used.", None),
    ],
)
def test_note_number(line, expected):
    assert note_number(line) == expected


# --- Document codes (GG 101 Appendix A1) -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "discipline", "lifecycle"),
    [
        ("GG 101", "General principles & scheme governance", "General information"),
        ("CD 109", "Civil engineering", "Design"),
        ("CG 501", "Civil engineering", "General information"),
        ("LA 104", "Sustainability & environment", "Appraisal"),
        ("CS 450", "Civil engineering", "Inspection and assessment"),
    ],
)
def test_doc_code_decodes_discipline_and_lifecycle(raw, discipline, lifecycle):
    code = parse_doc_code(raw)

    assert code.discipline == discipline
    assert code.lifecycle == lifecycle
    assert str(code) == raw


def test_doc_code_accepts_missing_space():
    assert str(parse_doc_code("CD109")) == "CD 109"


@pytest.mark.parametrize("raw", ["XD 109", "CX 109", "CD 10", "TD 9/93", "cd 109"])
def test_rejects_codes_outside_the_scheme(raw):
    # TD 9/93 is the pre-2019 format, superseded by CD 109.
    with pytest.raises(ConventionError):
        parse_doc_code(raw)


# --- Page footer ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "code", "version", "published"),
    [
        (
            "Downloaded from https://www.standardsforhighways.co.uk on 22-Oct-2026, "
            "GG 101 Version 0.1.0, published: 29-Sep-2021",
            "GG 101",
            "Version 0.1.0",
            "29-Sep-2021",
        ),
        (
            "Downloaded from https://www.standardsforhighways.co.uk on 22-Oct-2026, "
            "CD 109  Revision 1, published: Mar-2020",
            "CD 109",
            "Revision 1",
            "Mar-2020",
        ),
    ],
)
def test_footer_gives_version_and_publication(line, code, version, published):
    footer = parse_footer(line)

    assert str(footer.doc_code) == code
    assert footer.version == version
    assert footer.published == published


def test_non_footer_text_returns_none():
    assert parse_footer("1.1 The requirements in this document shall be used.") is None


# --- Regression cases from code review ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Departures apply only to the 'shall' verb form.", "note"),
        ('GG 101 describes "should" as a recommendation.', "note"),
        ("Set out in (should) form.", "note"),
        ("The designer may, should the Overseeing Organisation agree, vary it.", "may"),
        ("Works between May and June are restricted.", "note"),
        ("Published in May 21 edition.", "note"),
        ("The layout shall be checked where 'may' appears in Table 2.1.", "shall"),
    ],
)
def test_mentions_of_verbs_are_not_uses(text, expected):
    assert classify_verbal_form(text) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [("NOTE 1. Text", 1), ("NOTE. Text", 0), ("NOTE 2018 figures apply", 0)],
)
def test_note_number_edge_cases(line, expected):
    assert note_number(line) == expected


@pytest.mark.parametrize(
    ("line", "code", "published"),
    [
        (
            "Downloaded from https://www.standardsforhighways.co.uk on 12 Jan 2024, "
            "CD  109 Revision 1 , published: Mar 2020",
            "CD 109",
            "Mar 2020",
        ),
        (
            "Downloaded from https://www.standardsforhighways.co.uk on Sat Jan 12 2024, "
            "GG 101 Version 0.1.0, published: 29-Sep-2021",
            "GG 101",
            "29-Sep-2021",
        ),
    ],
)
def test_footer_tolerates_spacing_and_date_formats(line, code, published):
    footer = parse_footer(line)

    assert str(footer.doc_code) == code
    assert footer.published == published
