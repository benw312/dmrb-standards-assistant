"""Read the plotted markers off a DMRB chart image, as a draft for an engineer to check.

    uv run python -m ingest.digitise                      # every chart in data/charts/specs.yaml
    uv run python -m ingest.digitise CG501-Figure-7.5.4   # just one

The DMRB charts are embedded as raster images (about 300 dpi), not vector drawings, so the
points are read from pixels:

1. Gridlines are rows / columns that are dark across most of the image. Their pixel positions,
   paired with the values the spec says they mark, calibrate each axis (a straight-line fit;
   the fit residual is reported, so a skewed scan shows up).
2. Markers are the only solid shapes: eroding the dark pixels with a square wider than any line
   or text stroke leaves just the markers. Each one's centre is converted to chart values.
3. An overlay image with the readings drawn on is written for a person to compare by eye.

The output is a draft: `status` says it was machine-read, and it isn't used as ground truth
until an engineer has checked the overlay and changed `status` to `checked`.
"""

import argparse
import sys
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = ROOT / "data" / "charts"
SPECS = CHART_DIR / "specs.yaml"
CHECK_DIR = CHART_DIR / "check"  # overlays: git-ignored, they contain the Crown copyright image

DARK = 128  # grey level below which a pixel counts as ink
GRIDLINE_FRACTION = 0.5  # a gridline is ink across at least this share of the image
MARKER_GAP = 0.05  # a marker within this share of the x range of a spec x is matched to it
# Gates: a reading that fails one raises, rather than writing a plausible wrong number.
MAX_FIT_RESIDUAL = 0.005  # gridline misfit, as a share of the axis range (skewed scan)
MAX_X_MISMATCH = 0.01  # matched marker's x vs the spec's, as a share of the x range
MAX_SIDE_DISAGREEMENT = 0.25  # left vs right line readings, as a share of the marker size


class DigitiseError(RuntimeError):
    """The image doesn't match its spec (wrong gridline count, missing markers...)."""


class Axis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    unit: str
    gridlines: tuple[float, ...] = Field(min_length=2)  # values, low to high, frame included

    @model_validator(mode="after")
    def _increasing(self) -> "Axis":
        if list(self.gridlines) != sorted(set(self.gridlines)):
            raise ValueError("gridlines must be strictly increasing")
        return self


class Series(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    marker_x: tuple[float, ...] = Field(min_length=2)  # where the markers are, from the figure


class ChartSpec(BaseModel):
    """What a person reads off the figure once: page, axes, series. The digitiser does the rest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: str  # sources.yaml code, e.g. "CG 501"
    pdf: str  # filename in data/pdfs/
    page: int = Field(ge=1)
    clauses: tuple[str, ...] = Field(min_length=1)
    x: Axis
    y: Axis
    series: tuple[Series, ...] = Field(min_length=1)
    fails: Literal["below", "above"]  # which side of the line the clause says to avoid
    # Within this of the line (y units) a result is "marginal" and goes to an engineer. It's a
    # judgement about how precisely the chart can be read, so it's set by a person.
    tolerance: float = Field(gt=0)
    marker_size_px: int = Field(ge=5)  # roughly, at full resolution; sets the erosion size


@dataclass(frozen=True)
class Calibration:
    """pixel -> value for one axis, from a straight-line fit through the gridlines."""

    slope: float
    offset: float
    residual: float  # worst gridline misfit, in axis units

    def value(self, pixel: float) -> float:
        return self.slope * pixel + self.offset


@dataclass(frozen=True)
class Marker:
    px: float  # centre, image pixels
    py: float


# --- image analysis (pure functions over a boolean ink mask) -----------------------------


def ink(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L")) < DARK


@dataclass(frozen=True)
class Band:
    """A gridline: its centre and thickness in pixels."""

    centre: float
    width: int


def grid_bands(mask: np.ndarray, axis: int) -> list[Band]:
    """Lines running the length of the image. axis=1: horizontal lines (by row)."""
    coverage = mask.mean(axis=axis)
    rows = np.flatnonzero(coverage >= GRIDLINE_FRACTION)
    if rows.size == 0:
        return []
    # Consecutive rows are one (thick) line.
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    return [Band(float(g.mean()), len(g)) for g in groups]


def gridlines(mask: np.ndarray, axis: int) -> list[float]:
    return [b.centre for b in grid_bands(mask, axis)]


def calibrate(pixels: Sequence[float], values: Sequence[float], name: str) -> Calibration:
    if len(pixels) != len(values):
        raise DigitiseError(
            f"{name} axis: found {len(pixels)} gridlines at pixels "
            f"{[round(p) for p in pixels]}, but the spec lists {len(values)} values"
        )
    slope, offset = np.polyfit(pixels, values, 1)
    misfit = max(abs(slope * p + offset - v) for p, v in zip(pixels, values, strict=True))
    return Calibration(float(slope), float(offset), float(misfit))


def _erode_1d(mask: np.ndarray, k: int, axis: int) -> np.ndarray:
    """True where all k pixels centred here are true (a run-length test via cumulative sums)."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (k // 2 + 1, k // 2)
    c = np.cumsum(np.pad(mask, pad).astype(np.int32), axis=axis)
    hi = np.take(c, range(k, c.shape[axis]), axis=axis)
    lo = np.take(c, range(0, c.shape[axis] - k), axis=axis)
    return (hi - lo) == k


def solid(mask: np.ndarray, k: int) -> np.ndarray:
    """Erode with a k x k square: thin lines and text vanish, solid markers survive."""
    return _erode_1d(_erode_1d(mask, k, axis=0), k, axis=1)


def blobs(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (top, left, bottom, right) of 4-connected true regions."""
    seen = np.zeros_like(mask, dtype=bool)
    boxes = []
    for y, x in zip(*np.nonzero(mask), strict=True):
        if seen[y, x]:
            continue
        top, left, bottom, right = y, x, y, x
        queue = deque([(y, x)])
        seen[y, x] = True
        while queue:
            cy, cx = queue.popleft()
            top, bottom = min(top, cy), max(bottom, cy)
            left, right = min(left, cx), max(right, cx)
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                inside = 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]
                if inside and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        boxes.append((int(top), int(left), int(bottom), int(right)))
    return boxes


def markers(mask: np.ndarray, size_px: int) -> list[Marker]:
    """Where the markers are, roughly. Erode by about half a marker: the frame and the bold
    text are thinner than that. Box centres are only a first guess (a triangle's box centre
    isn't on the line); `on_line` refines them."""
    k = max(3, size_px // 2) | 1
    return [
        Marker(px=(left + right) / 2, py=(top + bottom) / 2)
        for top, left, bottom, right in blobs(solid(mask, k))
    ]


def _runs(column: np.ndarray) -> list[tuple[float, int]]:
    """(centre, length) of each ink run in one pixel column."""
    rows = np.flatnonzero(column)
    if rows.size == 0:
        return []
    runs = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    return [(float(r.mean()), len(r)) for r in runs]


def _is_gridline(run: tuple[float, int], bands: Sequence[Band]) -> bool:
    centre, length = run
    return any(abs(centre - b.centre) <= 1 and length <= b.width + 1 for b in bands)


def on_line(
    mask: np.ndarray,
    marker: Marker,
    size_px: int,
    plot: tuple[float, float],
    bands: Sequence[Band] = (),
) -> Marker:
    """The data point is where the plotted line passes the marker's x, whatever the marker's
    shape. The line is straight between markers but bends at them, so each side is read
    separately just clear of the marker, extended to the marker's x, and the sides averaged.
    At the chart's edges there is one side.

    `plot` is the frame's left and right pixel, so axis labels outside it are never read as
    the line. A run that is exactly a horizontal gridline (`bands`) is skipped. Where the line
    crosses or runs along a gridline the two merge into one run, which is kept: its centre is
    off by at most half the gridline's width.

    Raises if there's no line next to the marker, or the two sides disagree: either means
    the reading can't be trusted, and a wrong number would look just as plausible."""
    estimates = []
    for side in (-1, 1):
        samples = []
        for distance in (1.0, 1.5, 2.0, 3.0, 4.0):
            col = round(marker.px + side * distance * size_px)
            if not plot[0] < col < plot[1]:
                continue
            near = [
                run
                for run in _runs(mask[:, col])
                if abs(run[0] - marker.py) <= size_px and not _is_gridline(run, bands)
            ]
            if near:
                samples.append((col, min(near, key=lambda run: abs(run[0] - marker.py))[0]))
        if len(samples) >= 2:
            cols, ys = zip(*samples, strict=True)
            slope, offset = np.polyfit(cols, ys, 1)
            estimates.append(float(slope * marker.px + offset))
    where = f"marker at pixel ({marker.px:.0f}, {marker.py:.0f})"
    if not estimates:
        raise DigitiseError(f"no plotted line found next to the {where}")
    if len(estimates) == 2 and abs(estimates[0] - estimates[1]) > MAX_SIDE_DISAGREEMENT * size_px:
        raise DigitiseError(
            f"the line either side of the {where} disagrees by "
            f"{abs(estimates[0] - estimates[1]):.0f} px"
        )
    return Marker(px=marker.px, py=sum(estimates) / len(estimates))


# --- one chart --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    series: str
    x: float  # the spec's marker x (the figure plots markers at these values)
    y: float
    x_read: float  # what the pixels say x is: a check that the right marker was matched


def _assign(spec: ChartSpec, found: list[tuple[float, float]]) -> list[Reading]:
    """Match markers to series. At each marker x the series are stacked, so sorting the
    markers there by y and the series by their order in the spec pairs them up."""
    span = spec.x.gridlines[-1] - spec.x.gridlines[0]
    readings = []
    for x in sorted({x for s in spec.series for x in s.marker_x}):
        wanted = [s for s in spec.series if x in s.marker_x]
        here = sorted((p for p in found if abs(p[0] - x) <= MARKER_GAP * span), key=lambda p: p[1])
        if len(here) != len(wanted):
            raise DigitiseError(
                f"{spec.id}: expected {len(wanted)} marker(s) at x = {x}, found {len(here)}"
            )
        # Spec lists series bottom to top. Lines that cross or touch would pair up wrongly
        # here, so a spec must only list series that stay apart (true of the CG 501 charts).
        for s, (x_read, y) in zip(wanted, here, strict=True):
            if abs(x_read - x) > MAX_X_MISMATCH * span:
                raise DigitiseError(f"{spec.id}: the marker for x = {x} reads as x = {x_read:.3f}")
            readings.append(Reading(s.name, x, y, x_read))
    return readings


def _check_fit(spec: ChartSpec, axis: Axis, fit: Calibration, name: str) -> None:
    limit = MAX_FIT_RESIDUAL * (axis.gridlines[-1] - axis.gridlines[0])
    if fit.residual > limit:
        raise DigitiseError(
            f"{spec.id}: {name} gridlines are unevenly spaced (misfit {fit.residual:.4f} > "
            f"{limit:.4f}): wrong gridline values in the spec, or a skewed image"
        )


def read_chart(spec: ChartSpec, image: Image.Image) -> tuple[list[Reading], dict[str, float]]:
    mask = ink(image)
    columns = gridlines(mask, axis=0)
    cx = calibrate(columns, spec.x.gridlines, "x")
    # Image rows run downwards, so the bottom gridline is the lowest value.
    bands = grid_bands(mask, axis=1)
    cy = calibrate(sorted((b.centre for b in bands), reverse=True), spec.y.gridlines, "y")
    _check_fit(spec, spec.x, cx, "x")
    _check_fit(spec, spec.y, cy, "y")
    size, plot = spec.marker_size_px, (columns[0], columns[-1])
    found = [
        (cx.value(m.px), cy.value(m.py))
        for m in (on_line(mask, m, size, plot, bands) for m in markers(mask, size))
    ]
    return _assign(spec, found), {"x_fit_residual": cx.residual, "y_fit_residual": cy.residual}


def to_yaml(spec: ChartSpec, readings: list[Reading], fit: dict[str, float]) -> dict:
    curves = {
        s.name: [[r.x, round(r.y, 3)] for r in readings if r.series == s.name] for s in spec.series
    }
    return {
        "id": spec.id,
        "status": "draft",  # draft -> checked once an engineer has compared the overlay
        "digitised_by": f"ingest/digitise.py (machine-read {date.today().isoformat()})",
        "checked_by": None,
        "source": spec.source,
        "page": spec.page,
        "clauses": list(spec.clauses),
        "licence": "Contains public sector information licensed under the OGL v3.0",
        "x": {
            "name": spec.x.name,
            "unit": spec.x.unit,
            "min": spec.x.gridlines[0],
            "max": spec.x.gridlines[-1],
        },
        "y": {
            "name": spec.y.name,
            "unit": spec.y.unit,
            "min": spec.y.gridlines[0],
            "max": spec.y.gridlines[-1],
        },
        "fails": spec.fails,
        "tolerance": spec.tolerance,
        "curves": curves,
        "fit": {k: round(v, 4) for k, v in fit.items()},
        "worst_x_mismatch": round(max(abs(r.x_read - r.x) for r in readings), 4),
    }


def overlay(image: Image.Image, spec: ChartSpec, readings: list[Reading]) -> Image.Image:
    """The chart with each reading drawn as a red cross and labelled, for checking by eye."""
    mask = ink(image)
    cx = calibrate(gridlines(mask, axis=0), spec.x.gridlines, "x")
    cy = calibrate(sorted(gridlines(mask, axis=1), reverse=True), spec.y.gridlines, "y")
    out = image.convert("RGB")
    draw = ImageDraw.Draw(out)
    r = spec.marker_size_px
    for reading in readings:
        px = (reading.x - cx.offset) / cx.slope
        py = (reading.y - cy.offset) / cy.slope
        draw.line([(px - r, py), (px + r, py)], fill="red", width=3)
        draw.line([(px, py - r), (px, py + r)], fill="red", width=3)
        draw.text((px + r // 2, py - 2 * r), f"{reading.y:.3f}", fill="red", font_size=r)
    return out


# --- files ------------------------------------------------------------------------------


def load_specs(path: Path = SPECS) -> list[ChartSpec]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        return [ChartSpec.model_validate(item) for item in raw]
    except (OSError, yaml.YAMLError, ValidationError) as err:
        raise DigitiseError(f"{path.name}: {err}") from err


def page_image(pdf: Path, page: int) -> Image.Image:
    """The largest image embedded on a page, at its native resolution."""
    import pypdfium2 as pdfium  # dev dependency, only needed here

    doc = pdfium.PdfDocument(pdf)
    try:
        images = [
            obj.get_bitmap(render=False).to_pil()
            for obj in doc[page - 1].get_objects()
            if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE
        ]
    finally:
        doc.close()
    if not images:
        raise DigitiseError(f"{pdf.name} p{page}: no embedded image")
    return max(images, key=lambda im: im.width * im.height)


def digitise(spec: ChartSpec) -> Path:
    image = page_image(ROOT / "data" / "pdfs" / spec.pdf, spec.page)
    readings, fit = read_chart(spec, image)
    CHECK_DIR.mkdir(parents=True, exist_ok=True)
    overlay(image, spec, readings).save(CHECK_DIR / f"{spec.id}.png")
    out = CHART_DIR / f"{spec.id}.yaml"
    header = (
        f"# {spec.id}: machine-read by ingest/digitise.py. DRAFT until an engineer compares\n"
        f"# data/charts/check/{spec.id}.png with the figure and sets status: checked.\n"
    )
    body = yaml.safe_dump(to_yaml(spec, readings, fit), sort_keys=False, allow_unicode=True)
    out.write_text(header + body, encoding="utf-8")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ids", nargs="*", help="chart ids (default: all in specs.yaml)")
    args = parser.parse_args(argv)
    try:
        specs = [s for s in load_specs() if not args.ids or s.id in args.ids]
        if not specs:
            raise DigitiseError(f"no spec matches {args.ids}")
        for spec in specs:
            print(f"{spec.id}: wrote {digitise(spec).relative_to(ROOT)}")
    except DigitiseError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
