"""search_standards: semantic search over the index, for the agent to call with its own query.

A table carries values but rarely the requirement that makes them binding: Table 5.1 lists
gradients, while cl. 5.1 says they "shall not" be exceeded. In the Phase 3 check, search
returned the table without the clause and the model called the limit advice. So every table
in the results brings the clauses that cite it (found by the index's `refs` field), up to a
small cap.
"""

import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.rag import CONTEXT_FIELDS, TOP_K
from index.search import Hit, Mode, SearchAPI, search

REFERRING_PER_TABLE = 2  # clauses fetched for each table in the results
MAX_REFERRING = 4  # cap on clauses added that way, so expansion can't swamp the results
_STANDARD = re.compile(r"\s*([A-Za-z]{2})\s*([0-9]{3})\s*", re.ASCII)

Embed = Callable[[str], Sequence[float]]


class SearchStandardsArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description="What to look for, in the words of the standard where you can",
    )
    standard: str | None = Field(
        default=None,
        description="Limit the search to one document, e.g. 'CD 109'. Leave out to search all",
    )

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @field_validator("standard")
    @classmethod
    def standard_code(cls, value: str | None) -> str | None:
        # Normalised to the index's "CD 109" form. The pattern allows only letters, a space
        # and digits, so the value is safe to put in an OData filter.
        if value is None:
            return None
        match = _STANDARD.fullmatch(value)
        if not match:
            raise ValueError(f"{value!r} is not a document code like 'CD 109'")
        return f"{match.group(1).upper()} {match.group(2)}"


def _referring_clauses(client: SearchAPI, hits: Sequence[Hit]) -> list[Hit]:
    """Clauses whose refs point at a table in `hits`, not already among them."""
    have = {h.chunk_id for h in hits}
    extra: list[Hit] = []
    for hit in hits:
        if hit.fields.get("kind") != "table":
            continue
        # citation_id comes from our own index; quotes are escaped anyway.
        table = hit.citation_id.replace("'", "''")
        odata = f"kind eq 'clause' and refs/any(r: r eq '{table}')"
        for found in search(
            client, "*", None, Mode.KEYWORD, REFERRING_PER_TABLE, odata, CONTEXT_FIELDS
        ):
            if found.chunk_id not in have and len(extra) < MAX_REFERRING:
                have.add(found.chunk_id)
                extra.append(found)
    return extra


def search_standards(client: SearchAPI, embed: Embed, **kwargs: object) -> list[Hit]:
    """Top 8 semantic hits, then the clauses that cite any table among them."""
    args = SearchStandardsArgs.model_validate(kwargs)
    odata = f"doc_code eq '{args.standard}'" if args.standard else None
    hits = search(
        client, args.query, embed(args.query), Mode.SEMANTIC, TOP_K, odata, CONTEXT_FIELDS
    )
    return [*hits, *_referring_clauses(client, hits)]
