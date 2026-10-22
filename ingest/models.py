"""The unit of retrieval: one DMRB clause, table or glossary block, with its citation metadata."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ingest.conventions import Verb

ChunkKind = Literal["clause", "table", "glossary"]
ClauseLevel = Literal["requirement", "advice", "section"]


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Unique: the citation ID, plus '#2', '#3' for split parts")
    citation_id: str = Field(description="What answers cite: CD109-5.3.1, CD109-Table-2.10")
    doc_code: str  # "CD 109"
    doc_title: str
    version: str  # as printed in the PDF footer, e.g. "Revision 1"
    label: str  # "5.3.1", "Table 2.10" or "Glossary"
    kind: ChunkKind
    clause_level: ClauseLevel | None = None  # from GG 101 numbering; None for tables/glossary
    requirement_type: Verb | None = None  # strongest verb in the clause body, excluding NOTEs
    heading_path: str  # "4. Horizontal alignment > Road camber and superelevation"
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    url: str  # link to the document, with #page=N
    refs: tuple[str, ...] = ()  # citation IDs this chunk points to (tables, other clauses)
    warnings: tuple[str, ...] = ()  # parsing doubts worth a look by eye
