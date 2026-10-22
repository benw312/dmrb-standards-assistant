"""Parse DMRB PDFs with Document Intelligence (prebuilt-layout) and cache the output.

    uv run python -m ingest.parse            # every source in data/sources.yaml
    uv run python -m ingest.parse "CD 109"   # just one
    uv run python -m ingest.parse --force    # ignore the cache

Layout parsing is billed per page, so the output is cached in data/parsed/ against the PDF's
SHA-256: a document is only re-parsed when the PDF itself changes. Before any billable call,
the PDF's own footer is checked against sources.yaml, so the wrong revision is never paid for.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ingest.conventions import parse_footer
from ingest.sources import Source, load_sources

PARSE_MODEL = "prebuilt-layout"
LAYOUT_USD_PER_1000_PAGES = 10.0  # Azure list price for prebuilt-layout (S0), Oct 2026
ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdfs"
PARSED_DIR = ROOT / "data" / "parsed"

# (first page text, page count) for a PDF. Injectable so tests need no real PDFs.
PageReader = Callable[[Path], tuple[str, int]]


class ParseError(RuntimeError):
    """A PDF can't or shouldn't be parsed (missing, or not the revision sources.yaml says)."""


class AnalyzeClient(Protocol):
    """The slice of DocumentIntelligenceClient this module uses."""

    def begin_analyze_document(self, model_id: str, body: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ParsedPaths:
    markdown: Path
    result_json: Path
    meta: Path


@dataclass(frozen=True)
class ParseOutcome:
    source: Source
    paths: ParsedPaths
    pages: int
    cached: bool


def estimate_cost_usd(pages: int) -> float:
    return pages * LAYOUT_USD_PER_1000_PAGES / 1000


def read_first_page(path: Path) -> tuple[str, int]:  # pragma: no cover - thin pypdf wrapper
    from pypdf import PdfReader

    reader = PdfReader(path)
    return reader.pages[0].extract_text() or "", len(reader.pages)


def check_footer(first_page_text: str, source: Source) -> None:
    """Raise unless the PDF's own footer names the code and version in sources.yaml."""
    footer = parse_footer(first_page_text)
    if footer is None:
        raise ParseError(f"{source.filename}: no standardsforhighways footer on page 1")
    if str(footer.doc_code) != source.code or footer.version != source.version:
        raise ParseError(
            f"{source.filename} is {footer.doc_code} '{footer.version}' but sources.yaml says "
            f"'{source.version}' for {source.code}"
        )


def _paths(source: Source, out_dir: Path) -> ParsedPaths:
    stem = out_dir / source.doc_id
    return ParsedPaths(
        stem.with_suffix(".md"), stem.with_suffix(".layout.json"), stem.with_suffix(".meta.json")
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cached(paths: ParsedPaths, pdf_sha: str) -> bool:
    if not (paths.meta.exists() and paths.markdown.exists() and paths.result_json.exists()):
        return False
    return json.loads(paths.meta.read_text()).get("pdf_sha256") == pdf_sha


def parse_source(
    source: Source,
    client: AnalyzeClient,
    pdf_dir: Path = PDF_DIR,
    out_dir: Path = PARSED_DIR,
    *,
    force: bool = False,
    page_reader: PageReader = read_first_page,
) -> ParseOutcome:
    pdf = pdf_dir / source.filename
    if not pdf.exists():
        raise ParseError(f"Missing PDF: {pdf.name} (expected in {pdf_dir})")
    first_page, pages = page_reader(pdf)
    check_footer(first_page, source)  # free check, before the billable call

    paths, pdf_sha = _paths(source, out_dir), _sha256(pdf)
    if not force and _cached(paths, pdf_sha):
        return ParseOutcome(source, paths, pages, cached=True)

    with pdf.open("rb") as body:
        result = client.begin_analyze_document(
            PARSE_MODEL, body, output_content_format="markdown"
        ).result()

    out_dir.mkdir(parents=True, exist_ok=True)
    paths.markdown.write_text(result.content)
    paths.result_json.write_text(json.dumps(result.as_dict()))
    meta = {
        "doc_code": source.code,
        "version": source.version,
        "pdf_sha256": pdf_sha,
        "model": PARSE_MODEL,
        "pages": pages,
        "parsed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    # Written last: if anything above fails, there's no metadata and the next run re-parses.
    paths.meta.write_text(json.dumps(meta, indent=2))
    return ParseOutcome(source, paths, pages, cached=False)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wires real services
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("codes", nargs="*", help='Doc codes to parse, e.g. "CD 109" (default: all)')
    parser.add_argument("--force", action="store_true", help="re-parse even if cached")
    args = parser.parse_args(argv)

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.identity import DefaultAzureCredential

    from core.settings import load_settings

    sources = [s for s in load_sources() if not args.codes or s.code in args.codes]
    if not sources:
        print(f"No sources match {args.codes}")
        return 1
    client = DocumentIntelligenceClient(load_settings().docintel_endpoint, DefaultAzureCredential())

    billed = 0
    for source in sources:
        try:
            outcome = parse_source(source, client, force=args.force)
        except ParseError as err:
            print(f"SKIP   {source.code}: {err}")
            continue
        if not outcome.cached:
            billed += outcome.pages
        status = "cached" if outcome.cached else "parsed"
        print(
            f"{status:<7}{source.code:<8}{outcome.pages:>4} pages -> {outcome.paths.markdown.name}"
        )
    print(f"\nBilled this run: {billed} pages, about ${estimate_cost_usd(billed):.2f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
