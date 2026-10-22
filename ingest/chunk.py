"""Split a parsed DMRB document into retrieval chunks: one per clause, table and glossary.

    uv run python -m ingest.chunk                  # all parsed documents -> data/chunks/*.jsonl
    uv run python -m ingest.chunk --show CD109-2.10

Rules, from GG 101 Appendix B and the decisions log:
- A clause starts at a clause number in the left margin (X.X requirement, X.X.X advice, plus
  annex E/ NI/ S/ and appendix A2.1 forms). Numbers elsewhere are cross-references or table
  values. The margin is measured per document, not hard-coded.
- NOTEs stay with the clause before them. Sub-headings end a clause and update the heading path.
- Tables are their own chunks (CD109-Table-2.10); clauses that mention them list them in refs.
  Tables split across pages are merged. Glossary tables (Term / Abbreviation / Symbol) become
  one glossary chunk. Other unnumbered tables (release notes, contents) are skipped.
- Text outside any clause (foreword, introduction, unnumbered appendices) is not indexed.
- A chunk is capped at ~800 tokens: long clauses are split at sentence ends, never merged.
"""

import argparse
import json
import re
import sys
import textwrap
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import ValidationError

from agent.citations import chunk_id, glossary_id, table_id
from ingest.conventions import (
    DOC_SERIES,
    NAA_COUNTRIES,
    ClauseKind,
    classify_verbal_form,
    clause_consistency_warning,
    note_number,
    parse_clause_number,
)
from ingest.fonts import HeadingLevel, level_lookup, read_bold_rows
from ingest.layout import Item, Line, TableBlock, read_layout
from ingest.models import Chunk
from ingest.sources import Source, load_sources

MAX_TOKENS = 800
CHARS_PER_TOKEN = 4  # rough for English; a cap doesn't need an exact tokenizer
MAX_CHUNK_CHARS = MAX_TOKENS * CHARS_PER_TOKEN
MARGIN_TOLERANCE_IN = 0.3
MAX_HEADING_WORDS = 12  # longer "headings" are clause text DI mis-tagged as a heading
SAME_ROW_IN = 0.1
CAPTION_MAX_GAP_IN = 3.0  # a caption line further above its table than this isn't its caption
CAPTION_LINE_GAP_IN = 0.3  # a wrapped caption's next line sits about 0.17in lower
MAX_UNTAGGED_HEADING_WORDS = 8
GLOSSARY_HEADERS = frozenset({"term", "abbreviation", "symbol"})

ROOT = Path(__file__).resolve().parent.parent
PARSED_DIR = ROOT / "data" / "parsed"
CHUNKS_DIR = ROOT / "data" / "chunks"
PDF_DIR = ROOT / "data" / "pdfs"

_NAA = "|".join(NAA_COUNTRIES)
_NUMBER = rf"(?:(?:{_NAA})/)?[A-Z]?\d+(?:\.\d+)*"
_CLAUSE_LINE = re.compile(rf"^((?:(?:{_NAA})/)?[A-Z]?\d+(?:\.\d+){{1,2}})\.?(?:\s+(.+))?$")
_SECTION_LINE = re.compile(rf"^((?:(?:{_NAA})/)?\d+\.|[A-Z]\d+)(?:\s+(.+))?$")
# Table numbers: 2.10, 5.18a, A.1, and 6.25N2 (a table belonging to NOTE 2 of clause 6.25).
_TABLE_NUMBER = rf"(?:(?:{_NAA})/)?[A-Z]?\.?\d+(?:\.\d+)*(?:N\d+|[a-z])?"
# A caption is "Table N" then a capitalised title; "Table A.1 shows ..." is prose.
_TABLE_CAPTION = re.compile(rf"^Table\s+({_TABLE_NUMBER})\s+[A-Z(]")
# Figure and equation captions are text, never headings (both are printed in bold).
_FIGURE_CAPTION = re.compile(rf"^(?:Figure|Equation)\s+{_TABLE_NUMBER}\s+[A-Z(]")
_NOTE_LABEL = re.compile(r"^NOTE(?:\s+\d+)?$")
_TABLE_MENTION = re.compile(rf"\bTables?\s+({_TABLE_NUMBER}(?:\s*(?:,|and|&)\s*{_TABLE_NUMBER})*)")
_CLAUSE_MENTION = re.compile(rf"\b(?:clause|paragraph)s?\s+({_NUMBER})")
_LIST_SEPARATOR = re.compile(r"\s*(?:,|\band\b|&)\s*")
# "CD 127 clause 3.2" or "CD 109 [Ref 1.N] Table 2.9": the mention belongs to that document.
_DOC_BEFORE = re.compile(rf"\b({DOC_SERIES})\s?(\d{{3}})\s*(?:\[[^\]]*\]\s*)?$")
_LIST_ITEM = re.compile(r"^(?:\d+\)|[a-z]\))\s")
_SENTENCE_END = re.compile(r"(?<=[.;:])(\s+)")  # captured, so NOTE line breaks survive splits


@dataclass
class _ClauseDraft:
    number: str
    page: int
    y: float
    heading_path: str
    lines: list[str] = field(default_factory=list)


@dataclass
class _TableDraft:
    number: str
    caption: str
    page: int
    heading_path: str
    header: tuple[str, ...]
    rows: list[tuple[str, ...]]


@dataclass
class _Glossary:
    page: int | None = None
    rows: list[str] = field(default_factory=list)


def _margin_x(items: Sequence[Item]) -> float | None:
    """Most common x of number-only lines: the clause-number column."""
    xs = Counter(
        round(i.x, 1)
        for i in items
        if isinstance(i, Line) and (_CLAUSE_LINE.match(i.text) or _SECTION_LINE.match(i.text))
    )
    if not xs:
        return None
    top = max(xs.values())
    return min(x for x, n in xs.items() if n == top)


class _Walker:
    """Walks lines and tables in reading order, collecting clause, table and glossary drafts."""

    def __init__(self, margin: float | None, heading_level: HeadingLevel | None = None) -> None:
        self.margin = margin
        self.heading_level = heading_level
        self.section: str | None = None
        self.section_pending_title = False
        self.heading: str | None = None  # 12pt bold
        self.subheading: str | None = None  # 10pt bold, under `heading`
        self.current: _ClauseDraft | None = None
        self.clauses: list[_ClauseDraft] = []
        self.tables: dict[str, _TableDraft] = {}
        self.glossary = _Glossary()
        self.caption: Line | None = None  # latest unused "Table X.Y ..." line
        self.skipped_table_pages: list[int] = []
        self.dropped_lines = 0

    @property
    def path(self) -> str:
        return " > ".join(p for p in (self.section, self.heading, self.subheading) if p)

    def _at_margin(self, line: Line) -> bool:
        return self.margin is not None and abs(line.x - self.margin) <= MARGIN_TOLERANCE_IN

    def _close(self) -> None:
        if self.current:
            self.clauses.append(self.current)
            self.current = None

    def line(self, line: Line, following: Sequence[Item] = ()) -> None:
        if self._at_margin(line) and (m := _CLAUSE_LINE.match(line.text)):
            self._close()
            self.caption = None  # an unused caption (prose "Table 3.1 shows") can't reach on
            self.current = _ClauseDraft(m.group(1), line.page, line.y, self.path)
            if m.group(2):
                self.current.lines.append(m.group(2))
        elif self._at_margin(line) and (m := _SECTION_LINE.match(line.text)):
            self._section(m.group(1), m.group(2))
        elif self._continues_caption(line, following):
            # A caption wrapped onto a second line (CD 123 Table 5.18a); DI tags both as headings.
            caption = self.caption
            self.caption = replace(caption, text=f"{caption.text} {line.text}", y=line.y)
        elif _FIGURE_CAPTION.match(line.text) or _NOTE_LABEL.match(line.text):
            self._clause_text(line)  # never headings, even when tagged or short (Figure 5.6)
        elif _TABLE_CAPTION.match(line.text):
            # Checked before headings: DI tags some captions as headings, which put "Table
            # 5.18a ..." and its second line into the heading path.
            self.caption = line  # belongs to the table below, not the clause above
        elif (
            # Real headings never start lowercase: that's a wrapped figure caption's second
            # line, tagged as a heading by DI (CD 123 Figure 6.19 "junctions where ...").
            line.heading
            and not line.text[:1].islower()
            and not self._is_clause_text(line)
            or self._is_untagged_heading(line, following)
        ):
            self._heading(line)
        else:
            self._clause_text(line)

    def _clause_text(self, line: Line) -> None:
        if self.current:
            self.current.lines.append(line.text)
        else:
            self.dropped_lines += 1  # foreword, introduction, unnumbered appendix text

    def _section(self, number: str, title: str | None) -> None:
        heading = f"{number} {title}" if title else number
        if self.section and (self.section == heading or self.section.startswith(f"{number} ")):
            return  # a repeated running header, not a new section
        self._close()
        self.caption = None
        self.section = heading
        self.section_pending_title = title is None
        self.heading = self.subheading = None

    def _level(self, line: Line) -> int | None:
        return self.heading_level(line.page, line.y, line.text) if self.heading_level else None

    def _heading(self, line: Line) -> None:
        text = line.text
        if self.section_pending_title:
            self.section = f"{self.section} {text}"
            self.section_pending_title = False
            return
        if text.startswith("Appendix "):
            self._close()
            self.section, self.heading, self.subheading = text, None, None
            return
        level = self._level(line)
        # With fonts, a "heading" that isn't 12pt or 10pt bold is something else DI or the
        # untagged-heading rule picked up: a running header ("CG 501 Version 2.2.0") or an
        # inline label. Labels ending in a colon ("Length of curve:") aren't headings either.
        if (self.heading_level and level is None) or text.endswith(":"):
            self._clause_text(line)
            return
        self._close()
        if _section_title(self.section) == text:
            return  # CG 501 repeats "Drainage from adjacent land" under section 6 of that name
        if level == 2 and self.heading:
            self.subheading = text
        else:
            # Level 1, or unknown (no PDF fonts): a heading replaces both levels, as before.
            self.heading, self.subheading = text, None

    def _continues_caption(self, line: Line, following: Sequence[Item]) -> bool:
        """A line directly under a pending caption, in line with it, with the table next, and
        printed like a caption: bold by the fonts, or (without fonts) DI-tagged or lowercase."""
        c = self.caption
        if c is None or c.page != line.page or _TABLE_CAPTION.match(line.text):
            return False
        below = 0 < line.y - c.y <= CAPTION_LINE_GAP_IN and abs(line.x - c.x) <= SAME_ROW_IN
        table_next = any(isinstance(i, TableBlock) for i in following[:2])
        if self.heading_level:
            looks_like_caption = self._level(line) is not None
        else:
            looks_like_caption = line.heading or line.text[:1].islower()
        return below and table_next and looks_like_caption

    def _is_clause_text(self, line: Line) -> bool:
        """DI sometimes tags clause lines as headings; keep them as clause text when they're
        long, on the clause number's row, or finish a sentence the clause left open."""
        if len(line.text.split()) > MAX_HEADING_WORDS:
            return True
        c = self.current
        if not c:
            return False
        if c.page == line.page and abs(c.y - line.y) < SAME_ROW_IN:
            return True
        unfinished = bool(c.lines) and c.lines[-1][-1] not in ".;:"
        return unfinished and (line.text[0].islower() or line.text[0].isdigit())

    def _is_untagged_heading(self, line: Line, following: Sequence[Item]) -> bool:
        """A short, unpunctuated, capitalised line just before a new clause number (possibly
        via one more such line) is a sub-heading DI didn't tag (CD 109 "Obstructions to sight
        distance"; GG 101 "Departures from requirements" then "Scope"), not clause text."""
        if not _heading_like(line.text):
            return False
        for item in following[:2]:
            if not isinstance(item, Line):
                return False
            if self._at_margin(item) and (
                _CLAUSE_LINE.match(item.text) or _SECTION_LINE.match(item.text)
            ):
                return True
            if not _heading_like(item.text):
                return False
        return False

    def table(self, block: TableBlock) -> None:
        if block.header and block.header[0].strip().lower() in GLOSSARY_HEADERS:
            self.glossary.page = self.glossary.page or block.page
            self.glossary.rows += [f"{r[0]}: {' '.join(c for c in r[1:] if c)}" for r in block.rows]
            return
        # The caption line printed just above a table beats DI's own caption: DI attaches only
        # about 1 in 3 captions, and mislabelled CD 123 Table 5.18a as 5.18b.
        above = self.caption
        near = above and above.page == block.page and 0 <= block.y - above.y <= CAPTION_MAX_GAP_IN
        caption = above.text if near else block.caption
        self.caption = None  # a caption belongs to one table
        match = _TABLE_CAPTION.match(caption or "")
        if not match:
            self.skipped_table_pages.append(block.page)
            return
        number = match.group(1).rstrip(".")
        if number in self.tables:  # continued from the previous page
            self.tables[number].rows += list(block.rows)
            return
        caption = re.sub(r"\s*\(continued\)\s*$", "", caption, flags=re.IGNORECASE)
        self.tables[number] = _TableDraft(
            number, caption, block.page, self.path, block.header, list(block.rows)
        )


def _section_title(section: str | None) -> str | None:
    """'6. Drainage from adjacent land' -> 'Drainage from adjacent land'."""
    return section.split(" ", 1)[1] if section and " " in section else None


def _heading_like(text: str) -> bool:
    short = len(text.split()) <= MAX_UNTAGGED_HEADING_WORDS
    return text[:1].isupper() and text[-1] not in ".;:,)" and short


def _join(lines: Iterable[str]) -> str:
    """Join wrapped lines with spaces, but start NOTEs and list items on a new line."""
    text = ""
    for line in lines:
        new_para = note_number(line) is not None or _LIST_ITEM.match(line)
        text = f"{text}\n{line}" if text and new_para else f"{text} {line}" if text else line
    return text


def _split(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split at sentence ends so each part fits the cap. A clause is never merged with another.

    The whitespace between sentences is kept, so a NOTE's line break survives. A single
    sentence longer than the cap is wrapped between words as a last resort.
    """
    if len(text) <= limit:
        return [text]
    pieces = _SENTENCE_END.split(text)  # [sentence, gap, sentence, gap, ..., sentence]
    parts, current = [], ""
    for i in range(0, len(pieces), 2):
        sentence, gap = pieces[i], pieces[i - 1] if i else ""
        if current and len(current) + len(gap) + len(sentence) > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current}{gap}{sentence}" if current else sentence
    parts.append(current)
    return [w for p in parts for w in (textwrap.wrap(p, limit) if len(p) > limit else [p])]


def _markdown_table(header: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> list[str]:
    def row(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    lines = [row(header), "|" + " --- |" * len(header)] if header else []
    return lines + [row(r) for r in rows]


def _refs(
    text: str, doc: str, own_id: str, clause_ids: set[str], table_ids: set[str]
) -> tuple[str, ...]:
    def local(match: re.Match[str]) -> bool:
        other = _DOC_BEFORE.search(text[: match.start()])
        return not other or f"{other.group(1)} {other.group(2)}" == doc

    found = [
        table_id(doc, n.rstrip("."))
        for m in _TABLE_MENTION.finditer(text)
        if local(m)
        for n in _LIST_SEPARATOR.split(m.group(1))
    ]
    found += [chunk_id(doc, m.group(1)) for m in _CLAUSE_MENTION.finditer(text) if local(m)]
    known = clause_ids | table_ids
    return tuple(dict.fromkeys(r for r in found if r in known and r != own_id))


def _pack(prefix: list[str], lines: list[str], limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Group whole lines (table rows, glossary entries) into parts under the cap.

    `prefix` (caption and header row) is repeated on every part so each part reads alone.
    """
    parts, current = [], list(prefix)
    for line in lines:
        if len(current) > len(prefix) and len("\n".join([*current, line])) > limit:
            parts.append("\n".join(current))
            current = list(prefix)
        current.append(line)
    parts.append("\n".join(current))
    return parts


def _common(source: Source, label: str, page: int, heading_path: str) -> dict:
    return {
        "doc_code": source.code,
        "doc_title": source.title,
        "version": source.version,
        "label": label,
        "page": page,
        "heading_path": heading_path,
        "url": f"{source.url}#page={page}",
    }


_LEVELS = {
    ClauseKind.REQUIREMENT: "requirement",
    ClauseKind.ADVICE: "advice",
    ClauseKind.SECTION: "section",
}


def _clause_chunks(drafts: list[_ClauseDraft], source: Source, table_ids: set[str]) -> list[Chunk]:
    clause_ids = {chunk_id(source.code, d.number) for d in drafts}
    seen: Counter[str] = Counter()
    chunks = []
    for draft in drafts:
        text = _join(draft.lines).strip()
        if not text:
            continue
        cid = chunk_id(source.code, draft.number)
        seen[cid] += 1
        warnings = [f"Duplicate clause number {draft.number}"] if seen[cid] > 1 else []
        base_id = cid if seen[cid] == 1 else f"{cid}#dup{seen[cid]}"
        number = parse_clause_number(draft.number)
        verb = classify_verbal_form(text.split("\nNOTE")[0])  # NOTEs carry no requirement
        if warning := clause_consistency_warning(number, verb):
            warnings.append(warning)
        common = _common(source, draft.number, draft.page, draft.heading_path)
        for k, part in enumerate(_split(text), start=1):
            chunks.append(
                Chunk(
                    id=base_id if k == 1 else f"{base_id}#{k}",
                    citation_id=cid,
                    kind="clause",
                    clause_level=_LEVELS[number.kind],
                    requirement_type=verb,
                    text=part,
                    refs=_refs(text, source.code, cid, clause_ids, table_ids),
                    warnings=tuple(warnings),
                    **common,
                )
            )
    return chunks


def _table_chunks(tables: Iterable[_TableDraft], source: Source) -> list[Chunk]:
    chunks = []
    for table in tables:
        cid = table_id(source.code, table.number)
        header_lines = _markdown_table(table.header, [])
        body = _markdown_table((), table.rows)
        parts = _pack([table.caption, "", *header_lines], body)
        common = _common(source, f"Table {table.number}", table.page, table.heading_path)
        chunks += [
            Chunk(
                id=cid if k == 1 else f"{cid}#{k}",
                citation_id=cid,
                kind="table",
                text=part,
                **common,
            )
            for k, part in enumerate(parts, start=1)
        ]
    return chunks


def _glossary_chunks(glossary: _Glossary, source: Source) -> list[Chunk]:
    if not glossary.rows or glossary.page is None:
        return []
    cid = glossary_id(source.code)
    parts = _pack([f"{source.code} terms, abbreviations and symbols", ""], glossary.rows)
    common = _common(source, "Glossary", glossary.page, "Terms, abbreviations and symbols")
    return [
        Chunk(
            id=cid if k == 1 else f"{cid}#{k}",
            citation_id=cid,
            kind="glossary",
            text=part,
            **common,
        )
        for k, part in enumerate(parts, start=1)
    ]


@dataclass(frozen=True)
class ChunkStats:
    skipped_table_pages: tuple[int, ...]  # uncaptioned, non-glossary tables, worth a look
    dropped_lines: int  # body text outside any clause


def build_chunks_with_stats(
    items: Sequence[Item], source: Source, heading_level: HeadingLevel | None = None
) -> tuple[list[Chunk], ChunkStats]:
    """`heading_level(page, y, text)` gives 1 or 2 from the PDF's fonts (ingest/fonts.py)."""
    walker = _Walker(_margin_x(items), heading_level)
    for index, item in enumerate(items):
        if isinstance(item, TableBlock):
            walker.table(item)
        else:
            walker.line(item, items[index + 1 : index + 3])
    walker._close()
    table_ids = {table_id(source.code, n) for n in walker.tables}
    chunks = [
        *_clause_chunks(walker.clauses, source, table_ids),
        *_table_chunks(walker.tables.values(), source),
        *_glossary_chunks(walker.glossary, source),
    ]
    return chunks, ChunkStats(tuple(walker.skipped_table_pages), walker.dropped_lines)


def build_chunks(
    items: Sequence[Item], source: Source, heading_level: HeadingLevel | None = None
) -> list[Chunk]:
    return build_chunks_with_stats(items, source, heading_level)[0]


# --- Command line ------------------------------------------------------------------------


def _summary(source: Source, chunks: list[Chunk], stats: ChunkStats) -> str:
    kinds = Counter(c.clause_level or c.kind for c in chunks)
    split = sum(1 for c in chunks if "#" in c.id and "#dup" not in c.id)
    warned = sum(1 for c in chunks if c.warnings and c.id == c.citation_id)
    longest = max((len(c.text) for c in chunks), default=0)
    return (
        f"{source.code:<7} {len(chunks):>4} chunks | {kinds['requirement']} requirements, "
        f"{kinds['advice']} advice, {kinds['table']} tables, {kinds['glossary']} glossary | "
        f"{split} split parts, {warned} with warnings | longest ~{longest // CHARS_PER_TOKEN} "
        f"tokens | uncaptioned tables skipped on pages {list(stats.skipped_table_pages)}, "
        f"{stats.dropped_lines} lines outside clauses"
    )


def read_chunks(chunks_dir: Path = CHUNKS_DIR) -> list[Chunk]:
    """Every chunk in `chunks_dir`/*.jsonl, validated. Raises if an ID appears twice."""
    chunks, seen = [], set()
    for path in sorted(chunks_dir.glob("*.jsonl")):
        for number, raw in enumerate(path.read_text().splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                chunk = Chunk.model_validate_json(raw)
            except ValidationError as err:
                raise ValueError(f"{path.name}:{number}: not a valid chunk ({err})") from None
            if chunk.id in seen:
                raise ValueError(f"{path.name}:{number}: duplicate chunk ID {chunk.id!r}")
            seen.add(chunk.id)
            chunks.append(chunk)
    return chunks


def _show(chunk_id_: str, out_dir: Path) -> int:  # pragma: no cover - console output
    for chunk in read_chunks(out_dir):
        if chunk.id == chunk_id_ or chunk.citation_id == chunk_id_:
            level, verb = chunk.clause_level or "-", chunk.requirement_type or "-"
            print(f"{chunk.id}  ({chunk.kind}, {level}, {verb})")
            print(f"{chunk.doc_code} {chunk.version} | {chunk.heading_path} | page {chunk.page}")
            print(f"{chunk.url}")
            if chunk.refs:
                print(f"refs: {', '.join(chunk.refs)}")
            for warning in chunk.warnings:
                print(f"warning: {warning}")
            print(f"\n{chunk.text}\n")
            return 0
    print(f"No chunk {chunk_id_!r} in {out_dir}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - file IO wrapper
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("codes", nargs="*", help='Doc codes, e.g. "CD 109" (default: all)')
    parser.add_argument("--show", metavar="ID", help="print one chunk, e.g. CD109-2.10")
    args = parser.parse_args(argv)
    if args.show:
        return _show(args.show, CHUNKS_DIR)

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    for source in load_sources():
        if args.codes and source.code not in args.codes:
            continue
        layout_path = PARSED_DIR / f"{source.doc_id}.layout.json"
        if not layout_path.exists():
            print(f"{source.code:<7} not parsed yet: run python -m ingest.parse")
            continue
        items = read_layout(json.loads(layout_path.read_text()))
        pdf = PDF_DIR / source.filename
        if pdf.exists():
            levels = level_lookup(read_bold_rows(pdf))
        else:
            levels = None
            print(f"{source.code:<7} PDF not found: headings get one level only")
        chunks, stats = build_chunks_with_stats(items, source, levels)
        out = CHUNKS_DIR / f"{source.doc_id}.jsonl"
        out.write_text("".join(c.model_dump_json() + "\n" for c in chunks))
        print(_summary(source, chunks, stats))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
