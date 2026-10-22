"""get_clause and get_table: fetch one clause or table by its citation, straight from the index.

Search finds clauses by meaning; these fetch by ID, so the agent can follow a reference it has
read ("as identified in Table 4.5") instead of hoping a search surfaces it. A long clause or
table is stored in parts (CD109-5.3.1, CD109-5.3.1#2, ...); they're reassembled in order, and
a table's repeated caption and header rows are printed once.
"""

import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.citations import CitationError, normalise_citation

MAX_PARTS = 50  # far more than any chunk has (the longest has 4)
_FIELDS = [
    "id",
    "citation_id",
    "doc_code",
    "version",
    "label",
    "kind",
    "requirement_type",
    "heading_path",
    "page",
    "url",
    "text",
    "refs",
]


class SearchAPI(Protocol):
    def search(self, search_text: str | None = None, **kwargs: Any) -> Any: ...


class ClauseNotFoundError(LookupError):
    """No clause or table with that citation in the index."""


class AmbiguousCitationError(LookupError):
    """Two different clauses share one number (the chunker marks the second '#dup2')."""


class LookupArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    citation: str = Field(
        min_length=1,
        description="One clause or table, e.g. 'CD 109 cl. 4.5', 'CD 109 Table 4.5' or 'CD109-4.5'",
    )


class Clause(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation_id: str
    doc_code: str
    version: str
    label: str
    kind: Literal["clause", "table", "glossary", "figure"]
    requirement_type: str | None
    heading_path: str
    page: int
    url: str
    text: str
    refs: tuple[str, ...]  # clauses, tables and indexed figures it points to, for the next lookup


_PART = re.compile(r"#(\d+)$")
_DUPLICATE = re.compile(r"#dup\d+")


def _part_number(chunk_id: str) -> int:
    """'CD109-5.3.1' -> 1, 'CD109-5.3.1#3' -> 3, 'CD109-5.3.1#dup2#2' -> 2."""
    match = _PART.search(chunk_id)
    return int(match.group(1)) if match else 1


def _join_parts(texts: list[str], kind: str) -> str:
    """Reassemble a split chunk.

    Table, glossary and figure parts each repeat the caption and header (up to and including
    the "| --- |" row, or the caption and blank line otherwise), which is printed once.
    Clause parts are consecutive sentences with nothing repeated, so they're joined as they are.
    """
    if kind == "clause":
        return "\n".join(texts)
    first = texts[0].splitlines()
    prefix_end = next((i + 1 for i, line in enumerate(first) if line.startswith("| ---")), 2)
    prefix = first[:prefix_end]
    joined = list(first)
    for text in texts[1:]:
        lines = text.splitlines()
        joined += lines[len(prefix) :] if lines[: len(prefix)] == prefix else lines
    return "\n".join(joined)


def _fetch(client: SearchAPI, citation_id: str) -> Clause:
    # OData string literals escape ' as ''. citation_id comes from normalise_citation, which
    # can't produce a quote today; escaping keeps that from mattering if it ever changes.
    odata = "citation_id eq '{}'".format(citation_id.replace("'", "''"))
    docs = list(client.search(search_text="*", filter=odata, select=_FIELDS, top=MAX_PARTS))
    if not docs:
        raise ClauseNotFoundError(f"{citation_id} is not in the index")
    clauses = {_DUPLICATE.search(d["id"]) is not None for d in docs}
    if len(clauses) > 1:
        raise AmbiguousCitationError(
            f"{citation_id} is printed twice in the standard with different text; "
            "check the PDF and cite with care"
        )
    docs.sort(key=lambda d: _part_number(d["id"]))
    first = docs[0]
    refs = tuple(dict.fromkeys(r for d in docs for r in d.get("refs") or ()))
    return Clause.model_validate(
        {k: first.get(k) for k in _FIELDS if k not in ("id", "text", "refs")}
        | {
            "text": _join_parts([d.get("text") or "" for d in docs], first.get("kind")),
            "refs": refs,
        }
    )


def _citation_id(kwargs: dict[str, object]) -> str:
    args = LookupArgs.model_validate(kwargs)
    try:
        return normalise_citation(args.citation)
    except CitationError as err:
        raise ValueError(str(err)) from None


def get_clause(client: SearchAPI, **kwargs: object) -> Clause:
    """Any clause, table, glossary or indexed figure by citation.

    Raises ClauseNotFoundError if it isn't indexed.
    """
    return _fetch(client, _citation_id(kwargs))


def get_table(client: SearchAPI, **kwargs: object) -> Clause:
    """A table by citation ('CD 109 Table 4.5'). Refuses a clause, so a wrong ID is caught."""
    citation_id = _citation_id(kwargs)
    if "-Table-" not in citation_id:
        raise ValueError(f"{citation_id} is not a table; cite it like 'CD 109 Table 4.5'")
    return _fetch(client, citation_id)
