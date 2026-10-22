from ingest.layout import Line, TableBlock, read_layout
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
