"""Rebuild reading order from Document Intelligence's line geometry.

DI's Markdown and paragraph order get scrambled around figures: on some pages it merges figure
labels, NOTEs, clauses and even the page footer into one paragraph. Its *lines*, though, each
carry an exact position. DMRB pages are single-column with clause numbers in the left margin,
so sorting lines by (page, row, x) gives the true reading order. Measured on the four parsed
standards: 0 clause numbers out of order, against 5-14 per document in the Markdown.

Lines inside figures, tables and page furniture (headers, footers, page numbers) are dropped;
figure captions are kept as "[Figure ...]" and tables come back as structured blocks.
"""

import re
from dataclasses import dataclass
from typing import Any

from ingest.conventions import parse_footer

# Lines whose vertical centres are within this of a row's first line share that row (a margin
# number and its text). Well under a DMRB line height (~0.17in), so rows never merge.
ROW_TOLERANCE_IN = 0.06
REGION_SLACK_IN = 0.02  # DI polygons are tight; allow a little slack when testing containment
FURNITURE_ROLES = frozenset({"pageHeader", "pageFooter", "pageNumber"})
HEADING_ROLES = frozenset({"title", "sectionHeading"})
_PAGE_NUMBER = re.compile(r"^\d{1,3}$")

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass(frozen=True)
class Line:
    page: int
    x: float
    y: float  # vertical centre, inches from the top of the page
    text: str
    heading: bool = False


@dataclass(frozen=True)
class TableBlock:
    page: int
    x: float
    y: float
    caption: str | None
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


Item = Line | TableBlock


def _box(polygon: list[float]) -> Box:
    xs, ys = polygon[0::2], polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _contains(region: Box, polygon: list[float]) -> bool:
    """True if the centre of `polygon` lies inside `region`."""
    x0, y0, x1, y1 = _box(polygon)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx0, ry0, rx1, ry1 = region
    s = REGION_SLACK_IN
    return rx0 - s <= cx <= rx1 + s and ry0 - s <= cy <= ry1 + s


def _regions(items: list[dict[str, Any]]) -> dict[int, list[Box]]:
    regions: dict[int, list[Box]] = {}
    for item in items:
        for region in item.get("boundingRegions", []):
            regions.setdefault(region["pageNumber"], []).append(_box(region["polygon"]))
    return regions


def _table_block(table: dict[str, Any]) -> TableBlock:
    grid: dict[int, dict[int, str]] = {}
    header_rows = set()
    spans: list[tuple[int, int, str]] = []
    for cell in table["cells"]:
        text = cell["content"].strip()
        row, col = cell["rowIndex"], cell["columnIndex"]
        grid.setdefault(row, {})[col] = text
        row_span, col_span = cell.get("rowSpan") or 1, cell.get("columnSpan") or 1
        if cell.get("kind") == "columnHeader":
            header_rows.add(row)
            # Header rows after the first are flattened into the body, so repeating a tall
            # header cell down into them would read as data ("Design speed (kph)" as a value).
            row_span = 1
        spans += [
            (r, c, text)
            for r in range(row, row + row_span)
            for c in range(col, col + col_span)
            if (r, c) != (row, col)
        ]
    # A merged cell applies to every row and column it spans (CG 501 Table 5.2's verge row
    # covers both storm columns), so its text is repeated in each rather than left blank.
    # Filled after all real cells, and only where empty, so a cell DI did report always wins.
    for r, c, text in spans:
        grid.setdefault(r, {}).setdefault(c, text)
    width = table.get("columnCount") or 1 + max(c for row in grid.values() for c in row)
    rows = [tuple(grid[r].get(c, "") for c in range(width)) for r in sorted(grid)]
    # Multi-row headers are flattened into the body; the first header row names the columns.
    header = rows[0] if 0 in header_rows else ()
    body = rows[1:] if header else rows
    x0, y0, _, _ = _box(table["boundingRegions"][0]["polygon"])
    caption = (table.get("caption") or {}).get("content")
    return TableBlock(
        table["boundingRegions"][0]["pageNumber"], x0, y0, caption, header, tuple(body)
    )


def _is_furniture_text(text: str) -> bool:
    """Footers and page numbers DI failed to tag (it sometimes merges them into body text)."""
    return bool(_PAGE_NUMBER.match(text)) or parse_footer(text) is not None


def _reading_order(items: list[Item]) -> list[Item]:
    """Sort top to bottom, then left to right within each visual row.

    Rows are clustered by distance from the row's first line, not by rounding y to fixed
    bands: a band edge can fall between a clause number and its own text (CD 109 cl. 2.2).
    """
    ordered: list[Item] = []
    row: list[Item] = []
    for item in sorted(items, key=lambda i: (i.page, i.y)):
        if row and (item.page != row[0].page or item.y - row[0].y > ROW_TOLERANCE_IN):
            ordered += sorted(row, key=lambda i: i.x)
            row = []
        row.append(item)
    return ordered + sorted(row, key=lambda i: i.x)


def read_layout(result: dict[str, Any]) -> list[Item]:
    """A DI layout result (as a dict) -> lines and tables in reading order."""
    paragraphs = result.get("paragraphs", [])
    figures, tables = result.get("figures", []), result.get("tables", [])
    furniture = _regions([p for p in paragraphs if p.get("role") in FURNITURE_ROLES])
    headings = _regions([p for p in paragraphs if p.get("role") in HEADING_ROLES])
    dropped = _regions(figures + tables)
    captions = {(f.get("caption") or {}).get("content", "").strip() for f in figures} - {""}

    items: list[Item] = [_table_block(t) for t in tables if t.get("boundingRegions")]
    for figure in figures:
        if caption := (figure.get("caption") or {}).get("content"):
            x0, y0, _, _ = _box(figure["boundingRegions"][0]["polygon"])
            page = figure["boundingRegions"][0]["pageNumber"]
            items.append(Line(page, x0, y0, f"[{caption.strip()}]"))

    for page in result.get("pages", []):
        number = page["pageNumber"]
        for line in page.get("lines", []):
            text, polygon = line["content"].strip(), line["polygon"]
            skip_regions = dropped.get(number, []) + furniture.get(number, [])
            if not text or text in captions or _is_furniture_text(text):
                continue
            if any(_contains(region, polygon) for region in skip_regions):
                continue
            heading = any(_contains(region, polygon) for region in headings.get(number, []))
            x0, y0, _, y1 = _box(polygon)
            items.append(Line(number, x0, (y0 + y1) / 2, text, heading))
    return _reading_order(items)
