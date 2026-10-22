import pytest

from ingest.layout import FigureBlock, Line, TableBlock, read_layout
from tests.layout_fixtures import FOOTER, MARGIN_X, Layout


def texts(items) -> list[str]:
    return [i.text for i in items if isinstance(i, Line)]


def test_orders_by_position_not_by_list_order():
    # DI lists page 9's figure region before its heading; position must win.
    layout = (
        Layout()
        .line(9, 5.0, "Alignment constraint (Ac) shall be calculated using Equation 2.2a.")
        .line(9, 1.0, "2. Design speed", x=MARGIN_X, role="sectionHeading")
        .line(9, 4.8, "2.2", x=MARGIN_X)
    )

    assert texts(read_layout(layout.build())) == [
        "2. Design speed",
        "2.2",
        "Alignment constraint (Ac) shall be calculated using Equation 2.2a.",
    ]


def test_margin_number_comes_before_text_on_the_same_row():
    layout = (
        Layout()
        .line(8, 2.0, "This document shall apply to WS2 roads.")
        .line(8, 2.01, "1.2", x=MARGIN_X)
    )

    assert texts(read_layout(layout.build())) == ["1.2", "This document shall apply to WS2 roads."]


def test_drops_page_furniture_figure_text_and_untagged_footers():
    layout = (
        Layout()
        .line(9, 0.3, "CD 109 Revision 1", role="pageHeader")
        .line(9, 1.0, "2.1", x=MARGIN_X)
        .line(9, 1.0, "For new rural roads, design speed shall be derived from Figure 2.1.")
        .figure(
            9,
            2.0,
            ["120A", "100B", "Design speed (kph)"],
            caption="Figure 2.1 Selection of design speed",
        )
        .line(9, 9.5, "9")  # page number DI didn't tag
        .line(9, 9.7, FOOTER)  # footer DI merged into body text
    )

    assert texts(read_layout(layout.build())) == [
        "2.1",
        "For new rural roads, design speed shall be derived from Figure 2.1.",
        "[Figure 2.1 Selection of design speed]",
    ]


def test_marks_heading_lines():
    layout = (
        Layout().line(10, 1.0, "Rural roads", role="sectionHeading").line(10, 1.5, "Body text.")
    )

    items = read_layout(layout.build())

    assert [(i.text, i.heading) for i in items] == [("Rural roads", True), ("Body text.", False)]


def test_tables_become_blocks_and_their_cell_lines_are_dropped():
    layout = Layout().table(
        12,
        3.0,
        [["Design speed (kph)", "120", "100"], ["SSD (m)", "295", "215"]],
        caption="Table 2.10 Design speed related parameters",
    )

    items = read_layout(layout.build())

    assert len(items) == 1
    table = items[0]
    assert isinstance(table, TableBlock)
    assert table.caption == "Table 2.10 Design speed related parameters"
    assert table.header == ("Design speed (kph)", "120", "100")
    assert table.rows == (("SSD (m)", "295", "215"),)
    assert table.page == 12


def test_pages_stay_in_order():
    layout = Layout().line(2, 1.0, "second page").line(1, 9.0, "first page")

    assert texts(read_layout(layout.build())) == ["first page", "second page"]


def test_number_and_text_on_one_visual_line_stay_together_across_a_band_edge():
    # Real CD 109 page 10 geometry: tops 9.827 vs 9.810 straddle a 0.05in rounding boundary,
    # which once glued clause 2.2's first line onto clause 2.1.
    layout = {
        "pages": [
            {
                "pageNumber": 10,
                "lines": [
                    {
                        "content": "Alignment constraint (Ac) shall be calculated",
                        "polygon": [1.364, 9.81, 6.9, 9.81, 6.9, 9.981, 1.364, 9.981],
                    },
                    {
                        "content": "2.2",
                        "polygon": [0.581, 9.827, 0.8, 9.827, 0.8, 9.953, 0.581, 9.953],
                    },
                    {
                        "content": "2.1 ends here.",
                        "polygon": [1.364, 9.5, 6.9, 9.5, 6.9, 9.65, 1.364, 9.65],
                    },
                ],
            }
        ],
        "paragraphs": [],
    }

    assert texts(read_layout(layout)) == [
        "2.1 ends here.",
        "2.2",
        "Alignment constraint (Ac) shall be calculated",
    ]


def test_merged_cells_repeat_their_text_in_every_spanned_cell():
    # CG 501 Table 5.2: the Verges row's text spans both storm columns.
    layout = Layout().table(
        29,
        3.0,
        [["Location", "1-in-1 year", "1-in-5 year"], ["Verges", "Surcharge limited to 1.5 m", ""]],
        caption="Table 5.2 Storm event requirements",
    )
    data = layout.build()
    data["tables"][0]["cells"] = [
        c for c in data["tables"][0]["cells"] if not (c["rowIndex"] == 1 and c["columnIndex"] == 2)
    ]
    merged = next(c for c in data["tables"][0]["cells"] if c["content"].startswith("Surcharge"))
    merged["columnSpan"] = 2

    (table,) = read_layout(data)

    assert table.rows == (("Verges", "Surcharge limited to 1.5 m", "Surcharge limited to 1.5 m"),)


def test_row_spans_repeat_down_the_rows():
    layout = Layout().table(
        35,
        3.0,
        [
            ["Minor road", "Traffic", "Flow"],
            ["Non A or B", "≥ 600", "Yes"],
            ["", "≥ 300", "Optional"],
        ],
    )
    data = layout.build()
    cells = data["tables"][0]["cells"]
    data["tables"][0]["cells"] = [
        c for c in cells if not (c["rowIndex"] == 2 and c["columnIndex"] == 0)
    ]
    next(c for c in cells if c["content"] == "Non A or B")["rowSpan"] = 2

    (table,) = read_layout(data)

    assert [r[0] for r in table.rows] == ["Non A or B", "Non A or B"]


def _table_with(cells, columns=3):
    return {
        "pages": [],
        "paragraphs": [],
        "figures": [],
        "tables": [
            {
                "rowCount": 1 + max(c["rowIndex"] for c in cells),
                "columnCount": columns,
                "cells": cells,
                "boundingRegions": [{"pageNumber": 1, "polygon": [1, 1, 5, 1, 5, 2, 1, 2]}],
            }
        ],
    }


def _cell(row, col, text, header=False, **span):
    return {"rowIndex": row, "columnIndex": col, "content": text, **span} | (
        {"kind": "columnHeader"} if header else {}
    )


def test_a_tall_header_cell_is_not_copied_into_the_second_header_row():
    # CD 123 Table 2.25: "Design speed (kph)" spans both header rows.
    cells = [
        _cell(0, 0, "Design speed (kph)", header=True, rowSpan=2),
        _cell(0, 1, "Stagger distance (metres)", header=True, columnSpan=2),
        _cell(1, 1, "Ghost island", header=True),
        _cell(1, 2, "Dual carriageway", header=True),
        _cell(2, 0, "85"),
        _cell(2, 1, "50"),
        _cell(2, 2, "65"),
    ]

    (table,) = read_layout(_table_with(cells))

    assert table.header == (
        "Design speed (kph)",
        "Stagger distance (metres)",
        "Stagger distance (metres)",
    )
    assert table.rows[0] == ("", "Ghost island", "Dual carriageway")


def test_a_span_never_overwrites_a_cell_di_reported():
    cells = [
        _cell(0, 0, "A", header=True),
        _cell(0, 1, "B", header=True),
        _cell(0, 2, "C", header=True),
        _cell(1, 0, "wide", columnSpan=3),
        _cell(1, 2, "own text"),
    ]

    (table,) = read_layout(_table_with(cells))

    assert table.rows == (("wide", "wide", "own text"),)


# --- Figure text (CG 501 Version 2.2.0 Figure 2.5, © Crown copyright, OGL v3.0) ---------------


def _figures(layout):
    return [i for i in read_layout(layout.build()) if isinstance(i, FigureBlock)]


def test_a_figure_carries_its_paragraphs_and_caption():
    layout = Layout().figure(
        15, 2.0, ["Road runoff", "Discharge to ground"], caption="Figure 2.5 Runoff"
    )

    (figure,) = _figures(layout)

    assert figure.caption == "Figure 2.5 Runoff"
    assert figure.paragraphs == ("Road runoff", "Discharge to ground")
    assert figure.page == 15


def test_an_unattached_caption_is_taken_from_the_first_paragraph():
    layout = Layout().figure(15, 2.0, ["Figure 2.5 Destination of road runoff", "Road runoff"])

    (figure,) = _figures(layout)

    assert figure.caption == "Figure 2.5 Destination of road runoff"
    assert figure.paragraphs == ("Road runoff",)


def test_bullets_read_as_lone_dots_become_list_lines_but_full_stops_stay():
    text = ". Is it practicable? . Would pumping be required? Ends here. Next sentence."
    layout = Layout().figure(15, 2.0, [text], caption="Figure 2.5 Runoff")

    (figure,) = _figures(layout)

    assert figure.paragraphs == (
        "- Is it practicable?\n- Would pumping be required? Ends here. Next sentence.",
    )


def test_furniture_and_non_paragraph_elements_are_not_figure_text():
    layout = Layout().figure(15, 2.0, ["Road runoff"], caption="Figure 2.5 Runoff")
    data = layout.build()
    data["paragraphs"].append({"content": "CG 501 Version 2.2.0", "role": "pageHeader"})
    data["figures"][0]["elements"] += [f"/paragraphs/{len(data['paragraphs']) - 1}", "/tables/0"]

    (figure,) = [i for i in read_layout(data) if isinstance(i, FigureBlock)]

    assert figure.paragraphs == ("Road runoff",)


@pytest.mark.parametrize(
    "text",
    ["Use e.g. the verge. Then stop.", "Q = 5 . 2 m", "wait . . . then", "Ends. next one"],
)
def test_text_that_only_looks_like_a_bullet_is_left_alone(text):
    layout = Layout().figure(15, 2.0, [text], caption="Figure 2.5 Runoff")

    (figure,) = _figures(layout)

    assert figure.paragraphs == (text,)


def test_a_first_paragraph_that_mentions_a_figure_is_not_taken_as_its_caption():
    layout = Layout().figure(15, 2.0, ["Figure 2.5 shows the options", "Road runoff"])

    (figure,) = _figures(layout)

    assert figure.caption is None
