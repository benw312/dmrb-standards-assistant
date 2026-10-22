"""Heading levels from the PDF's own fonts, which Document Intelligence doesn't report.

DMRB documents (GG 101 template) use three bold sizes, the same in every standard checked:
14.3pt section titles ("5. Vertical alignment"), 12pt headings ("Entry lanes, exit lanes and
storage capacity") and 10pt subheadings ("Lane widths"). DI tags all of them as headings
without a level, so without fonts a subheading would replace its parent in the heading path.

Bold runs are grouped into rows by position, so a line is only ever compared with the bold text
printed at its own height, never with similar wording elsewhere on the page.
"""

import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

HEADING_PT = 12.0
SUBHEADING_PT = 10.0
SIZE_TOLERANCE_PT = 0.5
# DI's y is a line's vertical centre; pypdf's is its baseline, ~0.05in lower for 10-12pt.
# Rows of body text are ~0.17in apart, so 0.1in finds the right row and never the next one.
ROW_TOLERANCE_IN = 0.1
POINTS_PER_INCH = 72

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoldRow:
    size: float  # rendered size in points
    y: float  # baseline, inches from the top of the page
    text: str


BoldRows = Mapping[int, Sequence[BoldRow]]  # page -> bold rows on it
HeadingLevel = Callable[[int, float, str], int | None]  # (page, y, text) -> 1, 2 or None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def heading_level(rows: BoldRows, page: int, y: float, text: str) -> int | None:
    """1 for a 12pt bold heading, 2 for a 10pt bold one, None if the line isn't bold text.

    The line must be the whole of a bold row at its height (allowing for DI joining or
    splitting a row's runs differently), so a bold word inside body text doesn't qualify.
    """
    line = _norm(text)
    near = [r for r in rows.get(page, ()) if abs(r.y - y) <= ROW_TOLERANCE_IN]
    if not line or not near:
        return None
    row = min(near, key=lambda r: abs(r.y - y))
    if _norm(row.text) != line:
        return None
    if abs(row.size - HEADING_PT) <= SIZE_TOLERANCE_PT:
        return 1
    if abs(row.size - SUBHEADING_PT) <= SIZE_TOLERANCE_PT:
        return 2
    return None


def group_rows(runs: Iterable[tuple[float, float, float, str]]) -> list[BoldRow]:
    """(size, x, y, text) runs -> rows: same size, same baseline, joined left to right."""
    rows: list[list[tuple[float, float, float, str]]] = []
    for run in sorted(runs, key=lambda r: (round(r[2], 2), r[1])):
        last = rows[-1][-1] if rows else None
        same_row = last and abs(last[2] - run[2]) <= ROW_TOLERANCE_IN / 2 and last[0] == run[0]
        if same_row:
            rows[-1].append(run)
        else:
            rows.append([run])
    return [
        BoldRow(row[0][0], row[0][2], re.sub(r"\s+", " ", "".join(r[3] for r in row)).strip())
        for row in rows
    ]


def rendered_size(size: float, tm: Sequence[float], cm: Sequence[float]) -> float:
    """Font size x text-matrix scale x page-matrix scale. hypot keeps rotated text right."""
    return round(size * math.hypot(tm[0], tm[1]) * math.hypot(cm[0], cm[1]), 1)


def read_bold_rows(pdf: Path) -> dict[int, list[BoldRow]]:
    """Bold rows per page (1-based). Warns if the PDF has no heading-sized bold text at all."""
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-encoding chatter on every page
    pages: dict[int, list[BoldRow]] = {}
    for number, page in enumerate(PdfReader(pdf).pages, start=1):
        height_in = float(page.mediabox.height) / POINTS_PER_INCH
        runs: list[tuple[float, float, float, str]] = []

        def visit(text, cm, tm, font, size, runs=runs, height_in=height_in) -> None:
            if not text.strip() or not font or "Bold" not in str(font.get("/BaseFont", "")):
                return
            # Position on the page = text matrix origin mapped through the page matrix.
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            runs.append((rendered_size(size, tm, cm), x, height_in - y / POINTS_PER_INCH, text))

        page.extract_text(visitor_text=visit)
        pages[number] = group_rows(runs)
    sizes = {r.size for rows in pages.values() for r in rows}
    if not any(abs(s - p) <= SIZE_TOLERANCE_PT for s in sizes for p in (HEADING_PT, SUBHEADING_PT)):
        logger.warning("%s: no 12pt or 10pt bold text; headings get one level only", pdf.name)
    return pages


def level_lookup(rows: BoldRows) -> HeadingLevel:
    return lambda page, y, text: heading_level(rows, page, y, text)
