"""Query the index in one of four modes, so recall can compare them on the same questions.

- keyword:  BM25 over heading_path and text
- vector:   nearest neighbours on the embedding only
- hybrid:   both, fused by Azure's Reciprocal Rank Fusion
- semantic: hybrid, then re-ranked by Azure's semantic ranker (the production mode)
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from azure.search.documents.models import VectorizedQuery

from index.schema import SEMANTIC_CONFIG, VECTOR_FIELD

# Hybrid and semantic queries pull this many vector candidates before fusing and re-ranking.
# 50 is what the semantic ranker re-ranks, so fewer would starve it.
VECTOR_CANDIDATES = 50
_SELECT = ["id", "citation_id"]


class Mode(StrEnum):
    KEYWORD = "keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    citation_id: str
    score: float
    reranker_score: float | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)  # extra fields asked for in `select`


class SearchAPI(Protocol):
    def search(self, search_text: str | None = None, **kwargs: Any) -> Any: ...


def query_args(
    query: str,
    vector: Sequence[float] | None,
    mode: Mode,
    top: int,
    odata_filter: str | None,
    select: Sequence[str] = (),
) -> dict[str, Any]:
    """Keyword arguments for SearchClient.search. Separate from `search` so tests can see them."""
    if not query.strip():
        raise ValueError("empty query")
    if top < 1:
        raise ValueError("top must be at least 1")
    if mode is not Mode.KEYWORD and vector is None:
        raise ValueError(f"{mode} search needs a query vector")

    columns = [*_SELECT, *(f for f in select if f not in _SELECT)]
    args: dict[str, Any] = {"top": top, "select": columns, "filter": odata_filter}
    if mode is not Mode.VECTOR:
        args["search_text"] = query
    if mode is not Mode.KEYWORD:
        k = top if mode is Mode.VECTOR else max(top, VECTOR_CANDIDATES)
        args["vector_queries"] = [
            VectorizedQuery(vector=list(vector), k_nearest_neighbors=k, fields=VECTOR_FIELD)
        ]
    if mode is Mode.SEMANTIC:
        args |= {
            "query_type": "semantic",
            "semantic_configuration_name": SEMANTIC_CONFIG,
            # Fail loudly rather than quietly return un-reranked results (e.g. over the free
            # tier's monthly semantic quota), which would make the recall numbers lie.
            "semantic_error_mode": "fail",
        }
    return args


def search(
    client: SearchAPI,
    query: str,
    vector: Sequence[float] | None,
    mode: Mode = Mode.SEMANTIC,
    top: int = 5,
    odata_filter: str | None = None,
    select: Sequence[str] = (),
) -> list[Hit]:
    """Top `top` hits, best first.

    `select` names extra index fields to return on each hit (e.g. "text", "heading_path").
    `odata_filter` is passed through as-is, e.g. "doc_code eq 'CD 109'". Callers must build it
    from trusted values or escape quotes (' -> ''): it is not a parameterised query.
    """
    results = client.search(**query_args(query, vector, mode, top, odata_filter, select))
    hits = []
    for doc in results:
        hits.append(
            Hit(
                chunk_id=doc["id"],
                citation_id=doc["citation_id"],
                score=doc["@search.score"],
                reranker_score=doc.get("@search.reranker_score"),
                fields={f: doc.get(f) for f in select},
            )
        )
        if len(hits) == top:  # the pager can fetch further pages; never need them
            break
    return hits
