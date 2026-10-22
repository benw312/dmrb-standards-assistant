"""Chunker tests. DMRB text: CD 109 Revision 1, © Crown copyright, Open Government Licence v3.0."""

from datetime import date

import pytest

from ingest.chunk import MAX_CHUNK_CHARS, build_chunks
from ingest.layout import Line, TableBlock
from ingest.sources import Source

SOURCE = Source(
    code="CD 109",
    title="Highway link design",
    version="Revision 1",
    revision_date=date(2020, 3, 1),
    url="https://www.standardsforhighways.co.uk/dmrb/cd109.pdf",
    filename="CD109.pdf",
)
M, T = 0.58, 1.37  # left margin and text indent, inches


def ln(page: int, y: float, text: str, x: float = T, heading: bool = False) -> Line:
    return Line(page, x, y, text, heading)


SCOPE = [
    ln(8, 1.0, "1. Scope", x=M, heading=True),
    ln(8, 1.3, "Aspects covered", heading=True),
    ln(8, 1.6, "1.1", x=M),
    ln(8, 1.6, "This document provides requirements and advice for all aspects of highway / road"),
    ln(8, 1.8, "link design and shall be used for both new and improved motorway and all-purpose"),
    ln(8, 2.0, "trunk roads."),
    ln(8, 2.3, "1.2", x=M),
    ln(8, 2.3, "This document shall apply to WS2 roads where they are equal to or less than 2km."),
    ln(8, 2.6, "Implementation", heading=True),
    ln(8, 2.9, "1.3", x=M),
    ln(8, 2.9, "This document shall be implemented forthwith on all schemes."),
]


def by_id(chunks):
    return {c.id: c for c in chunks}


# --- Clauses ----------------------------------------------------------------------


def test_one_chunk_per_clause_with_metadata():
    chunks = by_id(build_chunks(SCOPE, SOURCE))

    clause = chunks["CD109-1.1"]
    assert clause.citation_id == "CD109-1.1"
    assert clause.label == "1.1"
    assert clause.kind == "clause"
    assert clause.clause_level == "requirement"
    assert clause.requirement_type == "shall"
    assert clause.heading_path == "1. Scope > Aspects covered"
    assert clause.page == 8
    assert clause.url == "https://www.standardsforhighways.co.uk/dmrb/cd109.pdf#page=8"
    assert clause.text.startswith("This document provides requirements")
    assert clause.text.endswith("all-purpose trunk roads.")
    assert clause.doc_code == "CD 109" and clause.version == "Revision 1"


def test_sub_heading_updates_path_and_ends_previous_clause():
    chunks = by_id(build_chunks(SCOPE, SOURCE))

    assert chunks["CD109-1.3"].heading_path == "1. Scope > Implementation"
    assert "Implementation" not in chunks["CD109-1.2"].text


def test_note_stays_with_its_clause_but_not_in_verb_classification():
    items = [
        ln(9, 1.0, "2. Design speed", x=M, heading=True),
        ln(9, 1.3, "2.1", x=M),
        ln(9, 1.3, "For new rural roads, design speed should be derived from Figure 2.1."),
        ln(9, 1.6, "NOTE 1"),
        ln(9, 1.6, "In Figure 2.1 the design speeds shall be arranged in bands."),
    ]

    clause = build_chunks(items, SOURCE)[0]

    assert "\nNOTE 1 In Figure 2.1" in clause.text
    assert clause.requirement_type == "should"  # NOTE's 'shall' doesn't count


def test_advice_clause_level_and_consistency_warning():
    items = [
        ln(9, 1.0, "2. Design speed", x=M, heading=True),
        ln(9, 1.3, "2.1.1", x=M),
        ln(9, 1.3, "Design speed shall be checked."),  # advice worded as a requirement
    ]

    clause = build_chunks(items, SOURCE)[0]

    assert clause.clause_level == "advice"
    assert any("2.1.1" in w and "'shall'" in w for w in clause.warnings)


def test_numbers_outside_the_margin_are_not_clause_starts():
    items = [
        ln(44, 1.0, "7. Climbing lanes", x=M, heading=True),
        ln(44, 1.3, "7.6", x=M),
        ln(44, 1.3, "Climbing lanes should be terminated as shown in Figure"),
        ln(44, 1.5, "7.5"),  # a wrapped cross-reference, at the text indent
        ln(44, 1.7, "for the full width of the lane."),
    ]

    chunks = build_chunks(items, SOURCE)

    assert [c.id for c in chunks] == ["CD109-7.6"]
    assert "shown in Figure 7.5 for the full width" in chunks[0].text


def test_text_before_the_first_clause_is_not_indexed():
    items = [
        ln(4, 1.0, "Foreword", heading=True),
        ln(4, 1.3, "This document is published by National Highways."),
        *SCOPE,
    ]

    chunks = build_chunks(items, SOURCE)

    assert all("published by National Highways" not in c.text for c in chunks)


def test_unnumbered_heading_after_last_clause_stops_text_bleeding_in():
    items = [
        *SCOPE,
        ln(70, 0.5, "England National Application Annex to CD 109", heading=True),
        ln(70, 1.0, "This National Application Annex gives specific requirements."),
        ln(72, 1.0, "E/1. Departures", x=M, heading=True),
        ln(72, 1.3, "E/1.1", x=M),
        ln(72, 1.3, "The Overseeing Organisation shall be informed."),
    ]

    chunks = by_id(build_chunks(items, SOURCE))

    assert "National Application Annex" not in chunks["CD109-1.3"].text
    assert chunks["CD109-E/1.1"].heading_path == "E/1. Departures"


def test_section_number_alone_takes_title_from_next_heading():
    items = [
        ln(68, 1.0, "E/1.", x=M, heading=True),
        ln(68, 1.0, "Climate change", heading=True),
        ln(68, 1.3, "E/1.1", x=M),
        ln(68, 1.3, "Climate change allowances shall be applied."),
    ]

    assert build_chunks(items, SOURCE)[0].heading_path == "E/1. Climate change"


def test_appendix_clauses():
    items = [
        ln(64, 1.0, "Appendix A. Examples of signal-controlled junctions", heading=True),
        ln(64, 1.3, "A2", x=M),
        ln(64, 1.3, "Signal-controlled T-junctions", heading=True),
        ln(64, 1.6, "A2.1", x=M),
        ln(64, 1.6, "Figure A.2 shows an example layout."),
    ]

    clause = by_id(build_chunks(items, SOURCE))["CD109-A2.1"]

    assert clause.heading_path == "A2 Signal-controlled T-junctions"
    assert clause.requirement_type == "note"


def test_long_clause_is_split_never_merged():
    sentence = "The verge shall be provided in accordance with the requirements. "
    lines = [ln(8, 1.0 + 0.2 * i, sentence) for i in range(MAX_CHUNK_CHARS // len(sentence) + 5)]
    items = [ln(8, 0.5, "1. Scope", x=M, heading=True), ln(8, 1.0, "1.1", x=M), *lines]

    chunks = build_chunks(items, SOURCE)

    assert [c.id for c in chunks] == ["CD109-1.1", "CD109-1.1#2"]
    assert all(c.citation_id == "CD109-1.1" for c in chunks)
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)


def test_duplicate_clause_number_gets_a_warning_and_unique_id():
    items = [*SCOPE, ln(9, 1.0, "1.3", x=M), ln(9, 1.0, "Repeated by a parsing fault.")]

    ids = [c.id for c in build_chunks(items, SOURCE)]

    assert len(ids) == len(set(ids))


# --- Tables ----------------------------------------------------------------------


TABLE = TableBlock(
    page=12,
    x=1.4,
    y=3.0,
    caption="Table 2.10 Design speed related parameters",
    header=("Design speed (kph)", "120", "100"),
    rows=(("Stopping sight distance (m)", "295", "215"),),
)


def test_table_is_its_own_chunk_linked_from_its_clause():
    items = [
        ln(12, 1.0, "2. Design speed", x=M, heading=True),
        ln(12, 1.3, "2.10", x=M),
        ln(12, 1.3, "Design parameters shall be as given in Table 2.10."),
        TABLE,
    ]

    chunks = by_id(build_chunks(items, SOURCE))

    table = chunks["CD109-Table-2.10"]
    assert table.kind == "table" and table.label == "Table 2.10"
    assert table.text.startswith("Table 2.10 Design speed related parameters")
    assert "| Stopping sight distance (m) | 295 | 215 |" in table.text
    assert table.heading_path == "2. Design speed"
    assert chunks["CD109-2.10"].refs == ("CD109-Table-2.10",)


def test_uncaptioned_table_takes_caption_from_line_above():
    items = [
        ln(12, 1.3, "2.10", x=M),
        ln(12, 1.3, "Parameters shall be as given in Table 2.10."),
        ln(12, 2.8, "Table 2.10 Design speed related parameters"),
        TableBlock(12, 1.4, 3.0, None, TABLE.header, TABLE.rows),
    ]

    assert "CD109-Table-2.10" in by_id(build_chunks(items, SOURCE))


def test_continued_table_is_merged():
    second = TableBlock(
        13,
        1.4,
        1.0,
        "Table 2.10 Design speed related parameters (continued)",
        TABLE.header,
        (("Full overtaking sight distance (m)", "580", "490"),),
    )

    chunks = by_id(build_chunks([TABLE, second], SOURCE))

    text = chunks["CD109-Table-2.10"].text
    assert "295" in text and "580" in text
    assert text.count("| Design speed (kph) |") == 1


def test_unnumbered_non_glossary_table_is_skipped():
    items = [TableBlock(3, 1.4, 1.0, None, ("Document code", "Version"), (("CD 109", "1"),))]

    assert build_chunks(items, SOURCE) == []


# --- Glossary ----------------------------------------------------------------------


def test_terms_and_abbreviations_become_one_glossary_chunk():
    items = [
        TableBlock(
            6, 1.4, 1.0, None, ("Abbreviation", "Definition"), (("SSD", "Stopping sight distance"),)
        ),
        TableBlock(
            7,
            1.4,
            1.0,
            None,
            ("Term", "Definition"),
            (("Design speed", "The speed used to determine geometric parameters."),),
        ),
    ]

    chunks = build_chunks(items, SOURCE)

    assert [c.id for c in chunks] == ["CD109-Glossary"]
    glossary = chunks[0]
    assert glossary.kind == "glossary"
    assert "SSD: Stopping sight distance" in glossary.text
    assert "Design speed: The speed used to determine geometric parameters." in glossary.text


@pytest.mark.parametrize("header", [("Symbol", "Definition"), ("Term", "Definition")])
def test_glossary_headers(header):
    items = [TableBlock(6, 1.4, 1.0, None, header, (("V", "Design speed"),))]

    assert build_chunks(items, SOURCE)[0].kind == "glossary"


def test_caption_several_lines_above_its_table_is_still_found():
    # CD 123 Table 5.18a: clause text sits between the caption line and the table.
    items = [
        ln(35, 1.0, "5.18", x=M),
        ln(35, 1.0, "Nearside diverge lanes should be provided in accordance with Table 5.18a."),
        ln(35, 1.5, "Table 5.18a Criteria for provision of nearside diverge lanes"),
        *[ln(35, 1.7 + 0.2 * i, f"Explanatory line {i}.") for i in range(6)],
        TableBlock(35, 1.4, 3.2, None, ("Flow", "Provision"), (("> 600 veh/h", "Required"),)),
    ]

    assert "CD109-Table-5.18a" in by_id(build_chunks(items, SOURCE))


def test_a_caption_is_used_by_only_one_table():
    items = [
        ln(12, 1.0, "Table 2.10 Design speed related parameters"),
        TableBlock(12, 1.4, 1.5, None, TABLE.header, TABLE.rows),
        TableBlock(12, 1.4, 4.0, None, ("Other", "Data"), (("x", "y"),)),
    ]

    tables = [c for c in build_chunks(items, SOURCE) if c.kind == "table"]

    assert [t.id for t in tables] == ["CD109-Table-2.10"]
    assert "Other" not in tables[0].text


def test_caption_line_above_beats_a_wrong_di_caption():
    # CD 123 page 35: DI captioned Table 5.18a as "5.18b" and left 5.18b uncaptioned.
    b_caption = "Table 5.18b Criteria for all other major roads"
    items = [
        ln(35, 4.6, "Table 5.18a Criteria for A class major roads"),
        TableBlock(35, 1.4, 4.9, b_caption, ("Minor road", "A class"), (("100", "Required"),)),
        ln(35, 7.8, b_caption),
        TableBlock(35, 1.4, 8.1, None, ("Minor road", "Other"), (("200", "Required"),)),
    ]

    tables = by_id(build_chunks(items, SOURCE))

    assert "A class" in tables["CD109-Table-5.18a"].text
    assert "Other" in tables["CD109-Table-5.18b"].text


def test_note_table_number_suffix():
    # CD 123: "Table 6.25N2 Geometric parameters for a passing bay" belongs to NOTE 2 of 6.25.
    items = [
        ln(51, 1.0, "Table 6.25N2 Geometric parameters for a passing bay"),
        TableBlock(51, 1.4, 1.3, None, ("Expected vehicles", "P1"), (("Car", "3.0"),)),
    ]

    table = build_chunks(items, SOURCE)[0]

    assert table.id == "CD109-Table-6.25N2" and table.label == "Table 6.25N2"


# --- Regression cases from code review ---------------------------------------------


def test_caption_line_goes_to_its_table_not_the_clause_before():
    items = [
        ln(20, 1.0, "3.5", x=M),
        ln(20, 1.0, "Relaxations shall not exceed the steps in Table 3.5."),
        ln(20, 1.4, "Table 3.5 Number of steps permitted below desirable minimum"),
        TableBlock(20, 1.4, 1.7, None, ("Road type", "Steps"), (("Motorway", "1"),)),
    ]

    chunks = by_id(build_chunks(items, SOURCE))

    assert chunks["CD109-3.5"].text == "Relaxations shall not exceed the steps in Table 3.5."
    assert chunks["CD109-Table-3.5"].text.startswith("Table 3.5 Number of steps")


@pytest.mark.parametrize(
    "prose", ["Table A.1 shows the asset life-cycle structure.", "Table 5.2.", "Table 3.4 presents"]
)
def test_prose_starting_with_table_is_not_a_caption(prose):
    items = [
        ln(15, 1.0, "2.1", x=M),
        ln(15, 1.0, "The structure is set out below."),
        ln(15, 1.3, prose),
        TableBlock(15, 1.4, 1.6, None, ("Code", "Description"), (("G", "General"),)),
    ]

    chunks = build_chunks(items, SOURCE)

    assert [c.kind for c in chunks] == ["clause"]
    assert prose in chunks[0].text


def test_caption_far_above_a_table_is_not_used():
    items = [
        ln(15, 1.0, "Table 2.3 Layout constraint"),
        TableBlock(15, 1.4, 6.0, None, ("Code", "Description"), (("G", "General"),)),
    ]

    assert build_chunks(items, SOURCE) == []


def test_untagged_subheading_before_next_clause_is_a_heading():
    # CD 109: "Obstructions to sight distance" wasn't tagged as a heading by DI.
    items = [
        ln(19, 1.0, "3. Sight distance", x=M, heading=True),
        ln(19, 1.3, "3.3.1", x=M),
        ln(19, 1.3, "Sight distance should be checked in both directions."),
        ln(19, 1.7, "Obstructions to sight distance"),
        ln(19, 2.0, "3.4", x=M),
        ln(19, 2.0, "Obstructions shall not intrude into the envelope."),
    ]

    chunks = by_id(build_chunks(items, SOURCE))

    assert chunks["CD109-3.3.1"].text.endswith("both directions.")
    assert chunks["CD109-3.4"].heading_path == "3. Sight distance > Obstructions to sight distance"


def test_short_line_continuing_a_clause_is_not_a_heading():
    items = [
        ln(19, 1.0, "3.4", x=M),
        ln(19, 1.0, "Obstructions shall not intrude into the envelope shown in Figure"),
        ln(19, 1.2, "3.4 and Table 3.5."),
        ln(19, 1.5, "3.5", x=M),
        ln(19, 1.5, "Next clause."),
    ]

    assert by_id(build_chunks(items, SOURCE))["CD109-3.4"].text.endswith(
        "Figure 3.4 and Table 3.5."
    )


def test_references_to_other_standards_are_not_resolved_locally():
    items = [
        ln(30, 1.0, "6.9", x=M),
        ln(30, 1.0, "Changeovers shall follow CD 127 clause 3.2 and clause 3.2 of this document."),
        ln(30, 1.4, "3.2", x=M),
        ln(30, 1.4, "A local clause."),
    ]

    refs = by_id(build_chunks(items, SOURCE))["CD109-6.9"].refs

    assert refs == ("CD109-3.2",)  # only the local mention, once


def test_every_table_in_a_list_is_referenced():
    items = [
        ln(30, 1.0, "5.1", x=M),
        ln(30, 1.0, "Values shall be taken from Tables 5.1, 5.7 and 5.9."),
        *[
            item
            for i, n in enumerate(("5.1", "5.7", "5.9"))
            for item in (
                ln(31 + i, 1.0, f"Table {n} Values"),
                TableBlock(31 + i, 1.4, 1.3, None, ("A", "B"), (("1", "2"),)),
            )
        ],
    ]

    refs = by_id(build_chunks(items, SOURCE))["CD109-5.1"].refs

    assert refs == ("CD109-Table-5.1", "CD109-Table-5.7", "CD109-Table-5.9")


def test_split_keeps_note_line_breaks_and_never_cuts_words():
    from ingest.chunk import _split

    text = "Clause text shall apply. " * 3 + "\nNOTE A note about it."
    long_word_text = "word " * 50

    assert "\nNOTE" in "".join(_split(text, limit=60))
    assert all(not p.startswith("ord") for p in _split(long_word_text.strip(), limit=23))


def test_skipped_uncaptioned_tables_are_reported_with_pages():
    from ingest.chunk import build_chunks_with_stats

    items = [TableBlock(42, 1.4, 1.0, None, ("140 metres", "Taper angle"), (("1", "2"),))]

    _, stats = build_chunks_with_stats(items, SOURCE)

    assert stats.skipped_table_pages == (42,)


def test_heading_tagged_continuation_of_a_clause_stays_in_the_clause():
    # CD 123 cl. 3.3: DI tagged the whole clause as a heading; "3.3." finishes its sentence.
    items = [
        ln(22, 8.5, "Direct accesses", heading=True),
        ln(22, 8.76, "3.3", x=M, heading=True),
        ln(
            22, 8.77, "A visibility splay shall be provided in accordance with Figure", heading=True
        ),
        ln(22, 8.93, "3.3.", heading=True),
    ]

    clause = build_chunks(items, SOURCE)[0]

    assert clause.text.endswith("in accordance with Figure 3.3.")
    assert clause.heading_path == "Direct accesses"


def test_two_untagged_headings_in_a_row():
    # GG 101: "Departures from requirements" then "Scope", neither tagged, before cl. 2.3.
    items = [
        ln(11, 4.9, "2.2", x=M),
        ln(11, 4.9, "National Application Annexes shall be used where they exist."),
        ln(11, 5.5, "Departures from requirements"),
        ln(11, 5.78, "Scope"),
        ln(11, 6.03, "2.3", x=M),
        ln(11, 6.03, "Statutory and legislative requirements must always be followed."),
    ]

    chunks = by_id(build_chunks(items, SOURCE))

    assert chunks["CD109-2.2"].text.endswith("where they exist.")
    assert chunks["CD109-2.3"].heading_path.endswith("Scope")
