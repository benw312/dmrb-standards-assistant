"""Build small Document Intelligence-shaped layout results for tests.

Text is quoted from DMRB CD 109 Revision 1, © Crown copyright, reused under the Open
Government Licence v3.0. Coordinates are in inches, as Document Intelligence reports for PDFs.
"""

from typing import Any

FOOTER = (
    "Downloaded from https://www.standardsforhighways.co.uk on 22-Oct-2026, "
    "CD 109 Revision 1, published: Mar-2020"
)
MARGIN_X = 0.58
TEXT_X = 1.37


def box(x: float, y: float, w: float = 5.0, h: float = 0.15) -> list[float]:
    return [x, y, x + w, y, x + w, y + h, x, y + h]


class Layout:
    """Accumulates pages, lines, paragraphs, figures and tables in DI's JSON shape."""

    def __init__(self) -> None:
        self.pages: dict[int, list[dict[str, Any]]] = {}
        self.paragraphs: list[dict[str, Any]] = []
        self.figures: list[dict[str, Any]] = []
        self.tables: list[dict[str, Any]] = []

    def line(self, page: int, y: float, text: str, x: float = TEXT_X, role: str | None = None):
        polygon = box(x, y, w=0.4 if x == MARGIN_X else 5.0)
        self.pages.setdefault(page, []).append({"content": text, "polygon": polygon})
        if role:
            self.paragraphs.append(
                {
                    "content": text,
                    "role": role,
                    "boundingRegions": [
                        {"pageNumber": page, "polygon": box(x - 0.01, y - 0.01, 5.5, 0.17)}
                    ],
                }
            )
        return self

    def clause(self, page: int, y: float, number: str, *text_lines: str):
        self.line(page, y, number, x=MARGIN_X)
        for i, text in enumerate(text_lines):
            self.line(page, y + 0.2 * i, text)
        return self

    def figure(self, page: int, y: float, labels: list[str], caption: str | None = None):
        for i, label in enumerate(labels):
            self.line(page, y + 0.1 + 0.2 * i, label, x=2.0)
        figure: dict[str, Any] = {
            "boundingRegions": [
                {"pageNumber": page, "polygon": box(1.5, y, 5.0, 0.2 * len(labels) + 0.3)}
            ]
        }
        if caption:
            figure["caption"] = {"content": caption}
        self.figures.append(figure)
        return self

    def table(self, page: int, y: float, rows: list[list[str]], caption: str | None = None):
        cells = [
            {
                "rowIndex": r,
                "columnIndex": c,
                "content": text,
                **({"kind": "columnHeader"} if r == 0 else {}),
            }
            for r, row in enumerate(rows)
            for c, text in enumerate(row)
        ]
        for r, row in enumerate(rows):  # DI also reports table text as page lines
            self.line(page, y + 0.05 + 0.2 * r, " ".join(row), x=1.5)
        table: dict[str, Any] = {
            "rowCount": len(rows),
            "columnCount": len(rows[0]),
            "cells": cells,
            "boundingRegions": [
                {"pageNumber": page, "polygon": box(1.4, y, 5.2, 0.2 * len(rows) + 0.1)}
            ],
        }
        if caption:
            table["caption"] = {"content": caption}
        self.tables.append(table)
        return self

    def build(self) -> dict[str, Any]:
        return {
            "pages": [{"pageNumber": n, "lines": lines} for n, lines in sorted(self.pages.items())],
            "paragraphs": self.paragraphs,
            "figures": self.figures,
            "tables": self.tables,
        }
