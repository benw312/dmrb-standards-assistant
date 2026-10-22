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


def test_read_chunks_loads_every_file_and_rejects_duplicate_ids(tmp_path):
    from ingest.chunk import read_chunks

    first, second = build_chunks(SCOPE, SOURCE)[:2]
    (tmp_path / "CD109.jsonl").write_text(first.model_dump_json() + "\n\n")
    (tmp_path / "CD123.jsonl").write_text(second.model_dump_json() + "\n")
    (tmp_path / "review_sample.md").write_text("not a chunk file")

    assert [c.id for c in read_chunks(tmp_path)] == [first.id, second.id]

    (tmp_path / "CD123.jsonl").write_text(first.model_dump_json() + "\n")
    with pytest.raises(ValueError, match="duplicate chunk ID"):
        read_chunks(tmp_path)


def test_read_chunks_names_the_file_and_line_of_a_bad_row(tmp_path):
    from ingest.chunk import read_chunks

    (tmp_path / "CD109.jsonl").write_text(
        build_chunks(SCOPE, SOURCE)[0].model_dump_json() + "\n{}\n"
    )

    with pytest.raises(ValueError, match="CD109.jsonl:2: not a valid chunk"):
        read_chunks(tmp_path)


# --- Heading levels and captions (CD 123 Version 2.1.0, © Crown copyright, OGL v3.0) --------

CD123 = SOURCE.model_copy(update={"code": "CD 123", "title": "Geometric design of junctions"})
# What ingest/fonts.py reports for these lines in the real PDF, keyed by (page, text).
# Anything absent is not bold heading text.
LEVELS = {
    (55, "Entry lanes, exit lanes and storage capacity"): 1,
    (55, "Minor road traffic islands"): 1,
    (55, "Lane widths"): 2,
    (55, "Storage length"): 2,
    (35, "Diverge tapers and auxiliary lanes"): 1,
    (35, "Table 5.18a Criteria for provision of nearside diverging tapers on A class"): 2,
    (35, "major roads with a design speed of 85kph or greater"): 2,
    (47, "SLD and dual carriageway"): 2,
    (30, "Corner radii and corner radii tapers"): 1,
}


def _level(page: int, y: float, text: str) -> int | None:
    assert isinstance(y, float)  # the walker passes the line's position through
    return LEVELS.get((page, text))


def _paths(items, levels=_level) -> dict[str, str]:
    return {c.id: c.heading_path for c in build_chunks(items, CD123, levels)}


SIGNALS = [
    ln(55, 1.0, "7.", x=M, heading=True),
    ln(55, 1.0, "Geometric design of signal-controlled junctions", heading=True),
    ln(55, 1.4, "Entry lanes, exit lanes and storage capacity", heading=True),
    ln(55, 1.7, "Lane widths", heading=True),
    ln(55, 2.0, "7.7", x=M),
    ln(55, 2.0, "At new junctions, the minimum width of straight ahead lanes shall be 3.0 metres."),
    ln(55, 2.4, "Storage length", heading=True),
    ln(55, 2.7, "7.8", x=M),
    ln(55, 2.7, "Storage lengths shall accommodate the queue."),
    ln(55, 3.1, "Minor road traffic islands", heading=True),
    ln(55, 3.4, "7.9", x=M),
    ln(55, 3.4, "Physical traffic islands shall have an area of at least 4.5 square metres."),
]


def test_subheadings_sit_under_their_heading_and_headings_reset_them():
    paths = _paths(SIGNALS)

    section = "7. Geometric design of signal-controlled junctions"
    assert (
        paths["CD123-7.7"]
        == f"{section} > Entry lanes, exit lanes and storage capacity > Lane widths"
    )
    assert (
        paths["CD123-7.8"]
        == f"{section} > Entry lanes, exit lanes and storage capacity > Storage length"
    )
    assert paths["CD123-7.9"] == f"{section} > Minor road traffic islands"


def test_without_font_levels_each_heading_replaces_the_last():
    paths = _paths(SIGNALS, levels=None)

    assert paths["CD123-7.7"] == "7. Geometric design of signal-controlled junctions > Lane widths"


DIVERGE = [
    ln(35, 1.0, "5.", x=M, heading=True),
    ln(35, 1.0, "Geometric design of priority junctions", heading=True),
    ln(35, 1.4, "Diverge tapers and auxiliary lanes", heading=True),
    ln(35, 1.8, "5.18", x=M),
    ln(35, 1.8, "A nearside diverging taper shall be provided in accordance with Table 5.18a."),
    # DI tags both caption lines as headings.
    ln(
        35,
        4.64,
        "Table 5.18a Criteria for provision of nearside diverging tapers on A class",
        heading=True,
    ),
    ln(35, 4.81, "major roads with a design speed of 85kph or greater", heading=True),
    TableBlock(35, 1.4, 5.0, None, ("Minor road", "Flow"), (("A class", "Yes"),)),
    ln(35, 7.0, "NOTE", x=M),
    ln(35, 7.0, "Diverging tapers can also be provided below the thresholds."),
]


def test_a_heading_tagged_two_line_caption_names_its_table_not_the_section():
    chunks = {c.id: c for c in build_chunks(DIVERGE, CD123, _level)}

    table = chunks["CD123-Table-5.18a"]
    assert table.text.startswith(
        "Table 5.18a Criteria for provision of nearside diverging tapers on A class "
        "major roads with a design speed of 85kph or greater"
    )
    expected_path = "5. Geometric design of priority junctions > Diverge tapers and auxiliary lanes"
    assert table.heading_path == expected_path
    # The caption no longer closes clause 5.18, so the NOTE after the table stays with it.
    assert "NOTE Diverging tapers can also be provided" in chunks["CD123-5.18"].text


def test_a_line_under_a_caption_without_a_table_next_is_not_caption_text():
    items = [
        *DIVERGE[:5],
        ln(35, 4.64, "Table 5.18a Criteria for provision of nearside diverging tapers"),
        ln(35, 4.81, "The table applies to A class roads."),  # clause text, then no table
        ln(35, 5.2, "5.19", x=M),
        ln(35, 5.2, "Tapers shall be provided."),
    ]

    chunks = {c.id: c for c in build_chunks(items, CD123, _level)}

    assert "The table applies to A class roads." in chunks["CD123-5.18"].text


def test_a_heading_tagged_figure_caption_stays_with_its_clause():
    # CD 123 p48: a figure caption's second line, tagged as a heading by DI.
    items = [
        ln(47, 1.0, "6.", x=M, heading=True),
        ln(47, 1.0, "Geometric design of major road central treatments", heading=True),
        ln(47, 1.4, "SLD and dual carriageway", heading=True),
        ln(47, 7.1, "6.19", x=M),
        ln(47, 7.1, "A minimum of 3 metres overlap shall be provided."),
        ln(48, 1.7, "Figure 6.19 Overlap between islands at SLD and dual priority", heading=True),
        ln(
            48,
            1.9,
            "junctions where the right turn out of the minor road is prevented.",
            heading=True,
        ),
        ln(48, 5.4, "6.20", x=M),
        ln(48, 5.4, "A physical island shall be provided to separate them."),
    ]

    chunks = {c.id: c for c in build_chunks(items, CD123, _level)}

    assert chunks["CD123-6.20"].heading_path.endswith("> SLD and dual carriageway")
    assert chunks["CD123-6.19"].text.endswith("right turn out of the minor road is prevented.")


def test_a_short_untagged_figure_caption_before_a_clause_is_not_a_heading():
    # CD 123 p30: "Figure 5.6 Corner radii" sits just above clause 5.6.1.
    items = [
        ln(30, 1.0, "5.", x=M, heading=True),
        ln(30, 1.0, "Geometric design of priority junctions", heading=True),
        ln(30, 8.3, "Corner radii and corner radii tapers", heading=True),
        ln(30, 9.1, "5.6", x=M),
        ln(30, 9.1, "Corner radii shall be measured in accordance with Figure 5.6."),
        ln(30, 9.75, "Figure 5.6 Corner radii", x=3.36),
        ln(31, 1.5, "5.6.1", x=M),
        ln(31, 1.5, "Corner radii should suit the design vehicle."),
    ]

    paths = _paths(items)

    assert paths["CD123-5.6.1"].endswith("> Corner radii and corner radii tapers")


# --- Lines that look like headings but aren't (found in review) ----------------------------

ROAD_GEOMETRY = [
    ln(31, 1.0, "7.", x=M, heading=True),
    ln(31, 1.0, "Road geometry", heading=True),
    ln(31, 1.4, "Drainage of wide carriageways", heading=True),
    ln(31, 1.7, "Drainage flow paths", heading=True),
    ln(31, 2.0, "7.5", x=M),
    ln(31, 2.0, "Wide carriageways shall be designed to drain."),
]
ROAD_LEVELS = {"Drainage of wide carriageways": 1, "Drainage flow paths": 2}


def _road_level(page: int, y: float, text: str) -> int | None:
    return ROAD_LEVELS.get(text)


CG501 = SOURCE.model_copy(update={"code": "CG 501", "title": "Design of highway drainage"})


def _cg501_paths(extra):
    return {c.id: c.heading_path for c in build_chunks(ROAD_GEOMETRY + extra, CG501, _road_level)}


@pytest.mark.parametrize(
    "impostor",
    [
        ln(35, 1.25, "CG 501 Version 2.2.0", x=1.36),  # running header, untagged, not bold
        ln(35, 1.25, "Equation 4.2 Superelevation", heading=True),  # bold equation caption
        ln(35, 1.25, "NOTE", x=M, heading=True),  # DI-tagged NOTE label
        ln(35, 1.25, "Length of curve:", heading=True),  # inline bold label
    ],
    ids=["running-header", "equation", "note-label", "colon-label"],
)
def test_impostor_headings_do_not_replace_the_real_ones(impostor):
    later = [impostor, ln(35, 1.6, "7.5.4", x=M), ln(35, 1.6, "Combinations should be avoided.")]

    paths = _cg501_paths(later)

    assert paths["CG501-7.5.4"] == (
        "7. Road geometry > Drainage of wide carriageways > Drainage flow paths"
    )


def test_a_heading_repeating_the_section_title_is_skipped():
    items = [
        ln(30, 1.0, "6.", x=M, heading=True),
        ln(30, 1.0, "Drainage from adjacent land", heading=True),
        ln(30, 1.4, "Drainage from adjacent land", heading=True),
        ln(30, 1.7, "6.1", x=M),
        ln(30, 1.7, "Land drainage shall be intercepted."),
    ]
    levels = {"Drainage from adjacent land": 1}

    chunks = build_chunks(items, CG501, lambda p, y, t: levels.get(t))

    assert chunks[0].heading_path == "6. Drainage from adjacent land"


def test_a_subheading_before_any_heading_acts_as_a_heading():
    items = [
        ln(30, 1.0, "6.", x=M, heading=True),
        ln(30, 1.0, "Drainage from adjacent land", heading=True),
        ln(30, 1.4, "New and existing connections", heading=True),
        ln(30, 1.7, "6.1", x=M),
        ln(30, 1.7, "Connections shall be agreed."),
    ]

    chunks = build_chunks(items, CG501, lambda p, y, t: 2 if t.startswith("New") else None)

    assert chunks[0].heading_path == "6. Drainage from adjacent land > New and existing connections"


def test_a_plain_line_under_a_caption_is_not_joined_even_with_a_table_next():
    # Only bold lines continue a caption; "(dimensions in metres)" is printed in roman type.
    items = [
        *DIVERGE[:5],
        ln(
            35,
            4.64,
            "Table 5.18a Criteria for provision of nearside diverging tapers on A class",
            heading=True,
        ),
        ln(35, 4.81, "(dimensions in metres)"),
        TableBlock(35, 1.4, 5.0, None, ("Minor road", "Flow"), (("A class", "Yes"),)),
    ]

    chunks = {c.id: c for c in build_chunks(items, CD123, _level)}

    assert chunks["CD123-Table-5.18a"].text.startswith(
        "Table 5.18a Criteria for provision of nearside diverging tapers on A class\n"
    )


def test_an_unused_caption_does_not_carry_into_the_next_clause():
    items = [
        *DIVERGE[:5],
        ln(
            35,
            4.0,
            "Table 5.18a Criteria for provision of nearside diverging tapers on A class",
            heading=True,
        ),
        ln(35, 4.3, "5.19", x=M),
        ln(35, 4.3, "Tapers shall be provided."),
        TableBlock(36, 1.4, 1.0, "Table 5.20 Taper lengths", ("Speed", "Length"), (("85", "50"),)),
    ]

    chunks = {c.id: c for c in build_chunks(items, CD123, _level)}

    assert "CD123-Table-5.20" in chunks and "CD123-Table-5.18a" not in chunks
