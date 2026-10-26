"""Read CD 109 Figure 2.1 (design speed from Ac and Lc) off its image, as a draft to check.

    uv run python -m ingest.digitise_bands

Figure 2.1 isn't a marker chart like the CG 501 ones, so it has its own reader:

- **Bands:** the y axis is design speed bands (120A at the top down to 70B), drawn as
  horizontal lines across the plot. Their pixel rows are the band edges.
- **Ac axis:** calibrated from the tick marks under the plot (0, 2, ..., 20).
- **Lc lines:** eight straight, nearly parallel lines (Lc 0, 5, ..., 35), dashed left of the
  Ac 6.6 divider and dash-dot right of it. With the gridlines removed, a search over slopes
  finds the one that stacks the remaining ink into the sharpest intercept peaks; each peak is
  one line, then each line is refitted on its own pixels.

The result is a table an engineer can check against the figure: for each Lc line, the Ac at
which it crosses each band edge. Reading a design speed is then: find where (Ac, Lc) falls
between those crossings. Worked example in NOTE 2 to cl. 2.1: Ac 12, Lc 15 gives 100A.
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import yaml
from PIL import Image, ImageDraw

from ingest.digitise import (
    CHART_DIR,
    CHECK_DIR,
    ROOT,
    DigitiseError,
    grid_bands,
    ink,
    page_image,
)

FIGURE_ID = "CD109-Figure-2.1"
PDF = "CD 109 revision 1 Highway link design-web.pdf"
PAGE = 10
BANDS = ("120A", "120B", "100A", "100B", "85A", "85B", "70A", "70B")  # top to bottom
LC_VALUES = (0, 5, 10, 15, 20, 25, 30, 35)  # top to bottom, as labelled on the right
AC_TICKS = tuple(range(0, 22, 2))
BAND_LINE_FRACTION = 0.5  # band edges run across most of the image
TICK_DEPTH_PX = (2, 8)  # tick marks hang this far below the bottom band edge
MIN_TICK_PX = 3  # ink rows in that strip for a column to count as a tick
SLOPES = np.arange(0.15, 0.45, 0.0005)  # px down per px across; the lines fall left to right
MIN_PEAK_PIXELS = 40  # an intercept peak with fewer pixels than this isn't a line
LINE_HALF_WIDTH_PX = 2.5


@dataclass(frozen=True)
class Line:
    lc: int
    slope: float  # px per px
    intercept: float  # y at x = 0, px
    pixels: int

    def y(self, x: float) -> float:
        return self.slope * x + self.intercept


@dataclass(frozen=True)
class Figure:
    edges: tuple[float, ...]  # band edge rows, top to bottom (len(BANDS) + 1)
    ac_px: tuple[float, float]  # pixel x of Ac 0 and of Ac 20
    lines: tuple[Line, ...]

    def px(self, ac: float) -> float:
        lo, hi = self.ac_px
        return lo + ac * (hi - lo) / AC_TICKS[-1]

    def ac(self, x: float) -> float:
        lo, hi = self.ac_px
        return (x - lo) / (hi - lo) * AC_TICKS[-1]


def _ticks(mask: np.ndarray, bottom: float) -> list[float]:
    top, depth = int(bottom) + TICK_DEPTH_PX[0], int(bottom) + TICK_DEPTH_PX[1]
    ink_cols = np.flatnonzero(mask[top:depth].sum(axis=0) >= MIN_TICK_PX)
    if ink_cols.size == 0:
        return []
    return [float(g.mean()) for g in np.split(ink_cols, np.flatnonzero(np.diff(ink_cols) > 1) + 1)]


def _line_pixels(mask: np.ndarray, edges: Sequence[float], columns: Sequence[float]):
    """Ink inside the plot with band edges and vertical lines removed."""
    m = mask.copy()
    for y in edges:
        m[max(0, int(y) - 2) : int(y) + 3, :] = False
    for x in columns:
        m[:, max(0, int(x) - 2) : int(x) + 3] = False
    m[: int(edges[0]) + 3, :] = False
    m[int(edges[-1]) - 2 :, :] = False
    m[:, : int(columns[0]) + 3] = False
    m[:, int(columns[-1]) - 2 :] = False
    return np.nonzero(m)


def _peaks(intercepts: np.ndarray) -> list[float]:
    # Padded by empty bins either side, so a line in the first or last bin is still a peak.
    bins = np.arange(intercepts.min() - 2, intercepts.max() + 3, 1.0)
    counts, _ = np.histogram(intercepts, bins=bins)
    found = []
    for i in range(1, len(counts) - 1):
        if (
            counts[i] >= MIN_PEAK_PIXELS
            and counts[i] >= counts[i - 1]
            and counts[i] > counts[i + 1]
        ):
            found.append(float(bins[i] + 0.5))
    return found


def read_figure(image: Image.Image) -> Figure:
    mask = ink(image)
    rows = grid_bands(mask, axis=1)
    edges = tuple(b.centre for b in rows)
    if len(edges) != len(BANDS) + 1:
        raise DigitiseError(f"expected {len(BANDS) + 1} band edges, found {len(edges)}")
    columns = [
        float(c) for c in np.flatnonzero(mask[int(edges[0]) : int(edges[-1])].mean(axis=0) > 0.35)
    ]
    columns = [
        float(g.mean())
        for g in np.split(np.array(columns), np.flatnonzero(np.diff(columns) > 1) + 1)
    ]
    ticks = _ticks(mask, edges[-1])
    if len(ticks) != len(AC_TICKS):
        raise DigitiseError(f"expected {len(AC_TICKS)} Ac ticks, found {len(ticks)}")
    fit = np.polyfit(ticks, AC_TICKS, 1)
    if max(abs(np.polyval(fit, t) - a) for t, a in zip(ticks, AC_TICKS, strict=True)) > 0.1:
        raise DigitiseError("Ac ticks are unevenly spaced")
    ac_px = (ticks[0], ticks[-1])

    ys, xs = _line_pixels(mask, edges, [columns[0], *columns[1:-1], columns[-1]])
    best = max(SLOPES, key=lambda s: (np.histogram(ys - s * xs, bins=200)[0] ** 2).sum())
    peaks = _peaks(ys - best * xs)
    if len(peaks) != len(LC_VALUES):
        raise DigitiseError(f"expected {len(LC_VALUES)} Lc lines, found {len(peaks)}")
    lines = []
    for lc, peak in zip(LC_VALUES, peaks, strict=True):
        on = np.abs(ys - best * xs - peak) < LINE_HALF_WIDTH_PX
        slope, intercept = np.polyfit(xs[on], ys[on], 1)
        lines.append(Line(lc, float(slope), float(intercept), int(on.sum())))
    return Figure(edges, ac_px, tuple(lines))


def crossings(figure: Figure) -> dict[int, dict[str, float | str | None]]:
    """For each Lc line: the band it starts in at Ac 0, then the Ac where it crosses down into
    each lower band (None: not between Ac 0 and 20)."""
    table = {}
    for line in figure.lines:
        row: dict[str, float | str | None] = {"at_ac_0": design_speed(figure, 0, line.lc)}
        for band, edge in zip(BANDS[1:], figure.edges[1:-1], strict=True):
            ac = figure.ac((edge - line.intercept) / line.slope)
            row[band] = round(ac, 2) if 0 <= ac <= AC_TICKS[-1] else None
        table[line.lc] = row
    return table


def design_speed(figure: Figure, ac: float, lc: float) -> str:
    """Band at (Ac, Lc): interpolate y between the Lc lines either side, find its band."""
    lcs = [line.lc for line in figure.lines]
    if not lcs[0] <= lc <= lcs[-1] or not 0 <= ac <= AC_TICKS[-1]:
        raise DigitiseError(f"Ac {ac}, Lc {lc} is outside Figure 2.1")
    x = figure.px(ac)
    ys = [line.y(x) for line in figure.lines]
    y = float(np.interp(lc, lcs, ys))
    for band, (top, bottom) in zip(
        BANDS, zip(figure.edges, figure.edges[1:], strict=False), strict=True
    ):
        if top <= y < bottom:
            return band
    return "above 120A" if y < figure.edges[0] else "below 70B"


def overlay(image: Image.Image, figure: Figure, scale: int = 3) -> Image.Image:
    """The figure enlarged, with each fitted Lc line drawn in red and the NOTE 2 point."""
    out = image.convert("RGB").resize((image.width * scale, image.height * scale))
    draw = ImageDraw.Draw(out)
    left, right = figure.px(0), figure.px(AC_TICKS[-1])
    for line in figure.lines:
        draw.line(
            [(left * scale, line.y(left) * scale), (right * scale, line.y(right) * scale)],
            fill="red",
            width=1,
        )
    x = figure.px(12) * scale
    y = float(
        np.interp(15, [ln.lc for ln in figure.lines], [ln.y(figure.px(12)) for ln in figure.lines])
    )
    draw.ellipse([x - 6, y * scale - 6, x + 6, y * scale + 6], outline="blue", width=2)
    return out


def to_yaml(figure: Figure) -> dict:
    return {
        "id": FIGURE_ID,
        "status": "draft",
        "digitised_by": f"ingest/digitise_bands.py (machine-read {date.today().isoformat()})",
        "checked_by": None,
        "source": "CD 109",
        "page": PAGE,
        "clauses": ["CD 109 cl. 2.1", "CD 109 cl. 2.4"],
        "licence": "Contains public sector information licensed under the OGL v3.0",
        "reads": "For each Lc line: the band at Ac 0, then the Ac at which the line crosses "
        "down into each lower band (null: not between Ac 0 and 20). The design speed is the "
        "band whose crossings bracket the Ac. 'above 120A': the line is above the top of the "
        "chart there.",
        "bands_top_to_bottom": list(BANDS),
        "lc_crossings": crossings(figure),
        "worked_example": {
            "ac": 12,
            "lc": 15,
            "expected": "100A",
            "read": design_speed(figure, 12, 15),
        },
        "lines": [
            {
                "lc": ln.lc,
                "slope_px": round(ln.slope, 4),
                "intercept_px": round(ln.intercept, 2),
                "pixels": ln.pixels,
            }
            for ln in figure.lines
        ],
    }


def main() -> int:
    CHECK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        figure = read_figure(page_image(ROOT / "data" / "pdfs" / PDF, PAGE))
        data = to_yaml(figure)
        if data["worked_example"]["read"] != "100A":
            raise DigitiseError(f"NOTE 2 example reads {data['worked_example']['read']}, not 100A")
    except DigitiseError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    overlay(page_image(ROOT / "data" / "pdfs" / PDF, PAGE), figure).save(
        CHECK_DIR / f"{FIGURE_ID}.png"
    )
    out = CHART_DIR / "design_speed" / f"{FIGURE_ID}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# {FIGURE_ID}: machine-read by ingest/digitise_bands.py. DRAFT until an engineer\n"
        "# checks the crossings against the figure and sets status: checked.\n"
    )
    out.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
