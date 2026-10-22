import pytest
from pydantic import ValidationError

from agent.tools.lookup import (
    AmbiguousCitationError,
    ClauseNotFoundError,
    get_clause,
    get_table,
)


def _doc(chunk_id, text, kind="clause", refs=(), page=21):
    citation_id = chunk_id.split("#")[0]
    return {
        "id": chunk_id,
        "citation_id": citation_id,
        "doc_code": "CD 109",
        "version": "Revision 1",
        "label": citation_id.split("-", 1)[1],
        "kind": kind,
        "requirement_type": "shall" if kind == "clause" else None,
        "heading_path": "4. Horizontal alignment > Relaxations",
        "page": page,
        "url": "https://example.org/cd109.pdf#page=21",
        "text": text,
        "refs": list(refs),
    }


class FakeSearch:
    def __init__(self, docs):
        self.docs = docs
        self.kwargs = {}

    def search(self, search_text=None, **kwargs):
        self.kwargs = {"search_text": search_text, **kwargs}
        # Parse the OData literal as Search would: strip the quotes, unescape '' to '.
        literal = kwargs["filter"].removeprefix("citation_id eq ")
        assert literal.startswith("'") and literal.endswith("'")
        wanted = literal[1:-1].replace("''", "'")
        return iter([d for d in self.docs if d["citation_id"] == wanted])


CLAUSE = _doc(
    "CD109-4.5",
    "Relaxations shall be permitted as identified in Table 4.5.",
    refs=["CD109-Table-4.5"],
)
HEADER = "Table 4.5 Steps permitted\n\n| Road type | Band | Steps |\n| --- | --- | --- |"
TABLE_PARTS = [
    _doc("CD109-Table-4.5#10", HEADER + "\n| row 10 | B | 1 |", kind="table"),
    _doc("CD109-Table-4.5", HEADER + "\n| Motorways | A | 2 |", kind="table"),
    _doc(
        "CD109-Table-4.5#2", HEADER + "\n| All-purpose | B | 4 |", kind="table", refs=["CD109-4.5"]
    ),
]


def test_get_clause_accepts_either_citation_form_and_filters_by_id():
    client = FakeSearch([CLAUSE])

    clause = get_clause(client, citation="CD 109 cl. 4.5")

    assert clause.citation_id == "CD109-4.5" and clause.refs == ("CD109-Table-4.5",)
    assert client.kwargs["filter"] == "citation_id eq 'CD109-4.5'"
    assert get_clause(client, citation="CD109-4.5") == clause


def test_parts_are_joined_in_numeric_order_with_the_header_printed_once():
    table = get_table(FakeSearch(TABLE_PARTS), citation="CD 109 Table 4.5")

    assert (
        table.text
        == HEADER + "\n| Motorways | A | 2 |\n| All-purpose | B | 4 |\n| row 10 | B | 1 |"
    )
    assert table.refs == ("CD109-4.5",)


def test_a_missing_citation_is_a_lookup_error():
    with pytest.raises(ClauseNotFoundError, match="CD109-9.9 is not in the index"):
        get_clause(FakeSearch([CLAUSE]), citation="CD 109 cl. 9.9")


def test_get_table_refuses_a_clause_citation():
    with pytest.raises(ValueError, match="not a table"):
        get_table(FakeSearch([CLAUSE]), citation="CD 109 cl. 4.5")


@pytest.mark.parametrize("citation", ["clause 4.5", "CD 109 cl. 4.5, 4.6", ""])
def test_unreadable_or_multiple_citations_are_refused(citation):
    with pytest.raises((ValueError, ValidationError)):
        get_clause(FakeSearch([CLAUSE]), citation=citation)


def test_clause_parts_keep_a_repeated_first_line():
    # Clause parts are consecutive sentences; nothing is a header, so nothing is dropped.
    parts = [_doc("CD109-1.1", "NOTE\nFirst part."), _doc("CD109-1.1#2", "NOTE\nSecond part.")]

    clause = get_clause(FakeSearch(parts), citation="CD109-1.1")

    assert clause.text == "NOTE\nFirst part.\nNOTE\nSecond part."


def test_a_glossary_prints_its_caption_once():
    caption = "CD 109 terms, abbreviations and symbols\n\n"
    parts = [
        _doc("CD109-Glossary", caption + "AADT: Annual average daily traffic", kind="glossary"),
        _doc("CD109-Glossary#2", caption + "WS2: Wide single", kind="glossary"),
    ]

    glossary = get_clause(FakeSearch(parts), citation="CD 109 Glossary")

    assert glossary.text == caption + "AADT: Annual average daily traffic\nWS2: Wide single"


def test_a_table_part_without_the_repeated_header_is_kept_whole():
    parts = [TABLE_PARTS[1], _doc("CD109-Table-4.5#2", "| Motorways | B | 3 |", kind="table")]

    table = get_table(FakeSearch(parts), citation="CD 109 Table 4.5")

    assert table.text.endswith("| Motorways | A | 2 |\n| Motorways | B | 3 |")


def test_two_clauses_sharing_a_number_are_refused_not_merged():
    # The chunker keeps a repeated clause number as CD109-2.1#dup2 with the same citation_id.
    parts = [
        _doc("CD109-2.1", "Design speed shall be derived from Figure 2.1."),
        _doc("CD109-2.1#dup2", "A different clause printed with the same number."),
        _doc("CD109-2.1#dup2#2", "Its second part."),
    ]

    with pytest.raises(AmbiguousCitationError, match="printed twice"):
        get_clause(FakeSearch(parts), citation="CD 109 cl. 2.1")


def test_the_filter_escapes_quotes_as_odata_requires():
    from agent.tools.lookup import _fetch

    client = FakeSearch([_doc("CD109-O'Neil", "text")])

    clause = _fetch(client, "CD109-O'Neil")

    assert client.kwargs["filter"] == "citation_id eq 'CD109-O''Neil'"
    assert clause.citation_id == "CD109-O'Neil"


def test_a_figure_prints_its_caption_once():
    caption = "Figure 2.5 Destination of road runoff\n\n"
    parts = [
        _doc("CG501-Figure-2.5", caption + "Road runoff", kind="figure"),
        _doc("CG501-Figure-2.5#2", caption + "Discharge to ground", kind="figure"),
    ]

    figure = get_clause(FakeSearch(parts), citation="CG 501 Figure 2.5")

    assert figure.kind == "figure"
    assert figure.text == caption + "Road runoff\nDischarge to ground"
