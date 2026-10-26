"""The Figure 2.1 reader, on a synthetic figure drawn with known lines. No PDFs needed."""

import pytest
from PIL import Image, ImageDraw

from ingest.digitise import DigitiseError
from ingest.digitise_bands import (
    AC_TICKS,
    BANDS,
    LC_VALUES,
    crossings,
    design_speed,
    read_figure,
    to_yaml,
)

LEFT, RIGHT = 110, 500  # Ac 0 and Ac 20
EDGES = (200, 246, 286, 323, 357, 387, 417, 444, 470)  # band edges, like the real figure
SLOPE = 0.27
INTERCEPTS = [129 + 25.3 * i for i in range(8)]  # Lc 0..35


def _figure(lines: int = 8, dashed: bool = True) -> Image.Image:
    im = Image.new("L", (590, 540), 255)
    d = ImageDraw.Draw(im)
    for i, y in enumerate(EDGES):
        d.line([(LEFT, y), (RIGHT, y)], fill=0, width=3 if i % 2 == 0 else 1)
    d.line([(LEFT, 180), (LEFT, EDGES[-1])], fill=0, width=3)
    d.line([(RIGHT, 180), (RIGHT, EDGES[-1])], fill=0, width=3)
    d.line([(238, 190), (238, EDGES[-1])], fill=0, width=1)  # the Ac 6.6 divider
    for i in range(len(AC_TICKS)):
        x = LEFT + i * (RIGHT - LEFT) / (len(AC_TICKS) - 1)
        d.line([(x, EDGES[-1]), (x, EDGES[-1] + 8)], fill=0, width=2)
    for b in INTERCEPTS[:lines]:
        x = LEFT + 3
        while x < RIGHT - 3:
            seg = 9 if dashed else 400
            x1 = min(x + seg, RIGHT - 3)
            y0, y1 = SLOPE * x + b, SLOPE * x1 + b
            if EDGES[0] < y0 < EDGES[-1] and EDGES[0] < y1 < EDGES[-1]:
                d.line([(x, y0), (x1, y1)], fill=0, width=2)
            x = x1 + 4
    return im


def test_finds_eight_lines_and_their_slope():
    fig = read_figure(_figure())
    assert [line.lc for line in fig.lines] == list(LC_VALUES)
    for line, b in zip(fig.lines, INTERCEPTS, strict=True):
        assert line.slope == pytest.approx(SLOPE, abs=0.01)
        assert line.y(300) == pytest.approx(SLOPE * 300 + b, abs=1.0)


def test_reads_a_band_from_ac_and_lc():
    fig = read_figure(_figure())
    # Lc 15 at Ac 12: x = 344, y = 0.27 * 344 + 204.9 = 297.8, between edges 286 and 323.
    assert design_speed(fig, 12, 15) == "100A"
    assert design_speed(fig, 0, 35) == BANDS[3]  # y = 0.27 * 110 + 306 = 335.7


def test_crossings_are_where_a_line_meets_each_band_edge():
    fig = read_figure(_figure())
    table = crossings(fig)
    row = table[15]
    # Lc 15 meets the 120B/100A edge (y 286) at x = (286 - 204.9) / 0.27 = 300.4 -> Ac 9.76
    assert row["into 100A"] == pytest.approx(9.76, abs=0.15)
    assert row["at_ac_0"] == "120A" and row["into 120A"] == "before 0"
    assert row["out of 70B"] == "after 20"
    # Lc 0 starts above the chart and enters it at y 200: x = (200 - 129) / 0.27 = 263 -> 7.8
    assert table[0]["at_ac_0"] == "above 120A"
    assert table[0]["into 120A"] == pytest.approx(7.8, abs=0.15)


def test_a_missing_line_is_refused():
    with pytest.raises(DigitiseError, match="expected 8 Lc lines"):
        read_figure(_figure(lines=7))


def test_outside_the_figure_is_refused():
    fig = read_figure(_figure())
    with pytest.raises(DigitiseError, match="outside Figure 2.1"):
        design_speed(fig, 21, 15)
    with pytest.raises(DigitiseError, match="outside Figure 2.1"):
        design_speed(fig, 10, 40)


def test_output_is_a_draft_with_the_worked_example():
    out = to_yaml(read_figure(_figure()))
    assert out["status"] == "draft" and out["checked_by"] is None
    assert out["worked_example"] == {"ac": 12, "lc": 15, "expected": "100A", "read": "100A"}


def _with(draw_extra) -> Image.Image:
    im = _figure()
    draw_extra(ImageDraw.Draw(im))
    return im


def test_an_extra_vertical_line_is_refused():
    im = _with(lambda d: d.line([(400, 190), (400, EDGES[-1])], fill=0, width=1))
    with pytest.raises(DigitiseError, match="frame and the Ac 6.6 divider"):
        read_figure(im)


def test_lines_that_arent_evenly_spaced_are_refused():
    im = Image.new("L", (590, 540), 255)
    base = _figure(lines=7)
    im.paste(base)
    d = ImageDraw.Draw(im)
    # An eighth "line" in the wrong place: too close to the seventh.
    b = INTERCEPTS[6] + 8
    d.line([(250, SLOPE * 250 + b), (495, SLOPE * 495 + b)], fill=0, width=2)
    with pytest.raises(DigitiseError, match="evenly spaced|too little ink|expected 8"):
        read_figure(im)


def test_just_above_a_band_edge_reads_the_band_above():
    fig = read_figure(_figure())
    assert design_speed(fig, 20, 35) == "70A"  # y = 0.27 * 500 + 306 = 441; 70A ends at 444


def test_an_axis_title_beside_the_plot_is_ignored():
    im = _with(lambda d: d.line([(570, 200), (570, 460)], fill=0, width=2))
    assert len(read_figure(im).lines) == 8
