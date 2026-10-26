"""The chart digitiser, on synthetic charts drawn with known points. No PDFs needed."""

import pytest
from PIL import Image, ImageDraw

from ingest.digitise import (
    Axis,
    ChartSpec,
    DigitiseError,
    Series,
    blobs,
    gridlines,
    ink,
    load_specs,
    read_chart,
    solid,
    to_yaml,
)

W, H, LEFT, TOP, RIGHT, BOTTOM = 1400, 900, 120, 40, 1340, 820
X_GRID = (0, 1, 2, 3, 4)
Y_GRID = (0, 0.5, 1.0, 1.5, 2.0)
SIZE = 30  # marker size, px


def _px(x: float, y: float) -> tuple[float, float]:
    return (
        LEFT + (x - X_GRID[0]) / (X_GRID[-1] - X_GRID[0]) * (RIGHT - LEFT),
        BOTTOM - (y - Y_GRID[0]) / (Y_GRID[-1] - Y_GRID[0]) * (BOTTOM - TOP),
    )


def _chart(series: dict[str, tuple[str, list[tuple[float, float]]]]) -> Image.Image:
    """A chart like the CG 501 ones: thick frame, thin grid, lines through solid markers,
    and some bold text that must not be read as a marker."""
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    for x in X_GRID:
        d.line([_px(x, Y_GRID[0]), _px(x, Y_GRID[-1])], fill=0, width=3)
    for y in Y_GRID:
        d.line([_px(X_GRID[0], y), _px(X_GRID[-1], y)], fill=0, width=3)
    d.rectangle([LEFT, TOP, RIGHT, BOTTOM], outline=0, width=6)
    d.text((LEFT + 300, TOP + 60), "5 No 3.65m Lanes", fill=0, font_size=28)
    d.text((10, TOP), "1.8", fill=0, font_size=28)  # an axis label, left of the frame
    r = SIZE / 2
    for shape, points in series.values():
        d.line([_px(*p) for p in points], fill=0, width=5)
        for p in points:
            cx, cy = _px(*p)
            if shape == "circle":
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
            elif shape == "square":
                d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=0)
            else:  # triangle: its box centre is NOT the data point
                d.polygon([(cx, cy - 1.4 * r), (cx - r, cy + 0.5 * r), (cx + r, cy + 0.5 * r)], 0)
    return im


def _spec(series: dict[str, tuple[str, list[tuple[float, float]]]]) -> ChartSpec:
    return ChartSpec(
        id="TEST-Figure-1",
        source="CG 501",
        pdf="x.pdf",
        page=1,
        clauses=("CG 501 cl. 7.5.1",),
        x=Axis(name="gradient", unit="%", gridlines=X_GRID),
        y=Axis(name="crossfall", unit="%", gridlines=Y_GRID),
        series=tuple(
            Series(name=n, marker_x=tuple(x for x, _ in pts)) for n, (_, pts) in series.items()
        ),
        fails="below",
        tolerance=0.02,
        marker_size_px=SIZE,
    )


THREE = {  # listed bottom to top, as the spec requires
    "low": ("circle", [(0, 0.40), (1, 0.47), (2, 0.55), (3, 0.62), (4, 0.71)]),
    "mid": ("square", [(0, 0.90), (1, 1.02), (2, 1.10), (3, 1.18), (4, 1.21)]),
    "high": ("triangle", [(0, 1.40), (1, 1.46), (2, 1.52), (3, 1.61), (4, 1.70)]),
}


def test_reads_every_marker_to_two_pixels_whatever_its_shape():
    readings, fit = read_chart(_spec(THREE), _chart(THREE))
    got = {(r.series, r.x): r.y for r in readings}
    for name, (_, points) in THREE.items():
        for x, y in points:
            assert got[(name, x)] == pytest.approx(y, abs=0.005), (name, x)
    assert fit["x_fit_residual"] < 0.01 and fit["y_fit_residual"] < 0.01


def test_gridlines_include_the_frame():
    mask = ink(_chart(THREE))
    assert len(gridlines(mask, axis=0)) == len(X_GRID)
    assert len(gridlines(mask, axis=1)) == len(Y_GRID)


def test_erosion_keeps_markers_and_drops_lines_and_text():
    mask = ink(_chart(THREE))
    assert len(blobs(solid(mask, SIZE // 2 | 1))) == 15


def test_a_spec_with_the_wrong_gridlines_is_refused_with_what_was_found():
    spec = _spec(THREE).model_copy(
        update={"x": Axis(name="g", unit="%", gridlines=(0, 1, 2, 3, 4, 5))}
    )
    with pytest.raises(DigitiseError, match="found 5 gridlines"):
        read_chart(spec, _chart(THREE))


def test_a_missing_marker_is_refused_not_guessed():
    one = {"low": ("circle", [(0, 0.4), (1, 0.5), (2, 0.6)])}
    spec = _spec({"low": ("circle", [(0, 0.4), (1, 0.5), (2, 0.6), (3, 0.7)])})
    with pytest.raises(DigitiseError, match="expected 1 marker"):
        read_chart(spec, _chart(one))


def test_output_is_a_draft_with_provenance():
    spec = _spec(THREE)
    readings, fit = read_chart(spec, _chart(THREE))
    out = to_yaml(spec, readings, fit)
    assert out["status"] == "draft" and out["checked_by"] is None
    assert "OGL" in out["licence"] and out["x"] == {
        "name": "gradient",
        "unit": "%",
        "min": 0,
        "max": 4,
    }
    assert [p[0] for p in out["curves"]["high"]] == [0, 1, 2, 3, 4]


def test_gridlines_must_increase():
    with pytest.raises(ValueError, match="increasing"):
        Axis(name="x", unit="m", gridlines=(0, 2, 1))


def test_the_committed_specs_load():
    specs = load_specs()
    assert {s.id for s in specs} >= {"CG501-Figure-7.5.4", "CG501-Figure-7.5.1"}


def test_a_marker_on_a_gridline_or_a_line_crossing_one_still_reads_true():
    on_grid = {"s": ("triangle", [(0, 1.5), (1, 1.48), (2, 1.0), (3, 1.03), (4, 1.5)])}
    readings, _ = read_chart(_spec(on_grid), _chart(on_grid))
    for r, (_, y) in zip(readings, on_grid["s"][1], strict=True):
        assert r.y == pytest.approx(y, abs=0.006), r
