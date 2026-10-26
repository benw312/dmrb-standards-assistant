"""Registry of indexed DMRB standards, loaded from data/sources.yaml.

Every chunk's citation (code, version, PDF link) traces back to an entry here, so a
DMRB revision means editing this file and rebuilding the index.
"""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator

from ingest.conventions import ConventionError, parse_doc_code

DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent.parent / "data" / "sources.yaml"


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    title: str
    version: str
    revision_date: date
    url: HttpUrl  # the document's page on standardsforhighways.co.uk
    pdf_url: HttpUrl | None = None  # direct PDF download
    filename: str
    # Figures whose text is worth indexing on its own, e.g. a flowchart ("2.5"). Most figures
    # are drawings, so this is a deliberate per-document choice (see docs/CHARTS.md).
    text_figures: tuple[str, ...] = ()

    @field_validator("code")
    @classmethod
    def code_is_dmrb_format(cls, value: str) -> str:
        try:
            return str(parse_doc_code(value))  # GG 101 Appendix A1 format, e.g. "CD 109"
        except ConventionError as err:
            raise ValueError(str(err)) from err

    @field_validator("version")
    @classmethod
    def version_is_filled_in(cls, value: str) -> str:
        # Placeholders would silently end up in every citation, so fail loudly instead.
        if not value.strip() or value.strip().upper() == "TODO":
            raise ValueError("version is still TODO: fill it in from standardsforhighways.co.uk")
        return value

    @field_validator("text_figures")
    @classmethod
    def figure_numbers_are_readable(cls, numbers: tuple[str, ...]) -> tuple[str, ...]:
        # Normalised like citations ("2.05" -> "2.5"), so a listed figure matches its caption,
        # and a number the citation grammar can't read fails here, not mid-chunking.
        from agent.citations import CitationError, clean_number

        try:
            return tuple(clean_number(n) for n in numbers)
        except CitationError as err:
            raise ValueError(f"text_figures: {err}") from None

    @property
    def doc_id(self) -> str:
        """Space-free form used in chunk IDs, e.g. 'CD109'."""
        return self.code.replace(" ", "")


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> list[Source]:
    if not path.exists():
        raise FileNotFoundError(f"Sources file not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    sources = [Source.model_validate(entry) for entry in raw.get("sources", [])]

    codes = [s.code for s in sources]
    duplicates = sorted({c for c in codes if codes.count(c) > 1})
    if duplicates:
        raise ValueError(f"Duplicate doc codes in {path.name}: {duplicates}")
    return sources
