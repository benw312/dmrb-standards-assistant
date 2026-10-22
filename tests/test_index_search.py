import inspect

import pytest
from azure.search.documents import SearchClient

from index.search import VECTOR_CANDIDATES, Hit, Mode, query_args, search
from tests.index_fixtures import vector

QUERY = "maximum gradient on a motorway"


def test_keyword_mode_sends_text_only():
    args = query_args(QUERY, None, Mode.KEYWORD, top=5, odata_filter=None)

    assert args["search_text"] == QUERY
    assert "vector_queries" not in args and "query_type" not in args


def test_vector_mode_sends_vector_only_with_k_equal_to_top():
    args = query_args(QUERY, vector(), Mode.VECTOR, top=5, odata_filter=None)

    assert "search_text" not in args
    (vq,) = args["vector_queries"]
    assert vq.k_nearest_neighbors == 5 and vq.fields == "embedding"


def test_hybrid_mode_sends_both_and_widens_vector_candidates():
    args = query_args(QUERY, vector(), Mode.HYBRID, top=5, odata_filter=None)

    assert args["search_text"] == QUERY
    assert args["vector_queries"][0].k_nearest_neighbors == VECTOR_CANDIDATES
    assert "query_type" not in args


def test_semantic_mode_reranks_and_fails_loudly():
    args = query_args(QUERY, vector(), Mode.SEMANTIC, top=5, odata_filter="doc_code eq 'CD 109'")

    assert args["query_type"] == "semantic"
    assert args["semantic_configuration_name"] == "dmrb-semantic"
    assert args["semantic_error_mode"] == "fail"
    assert args["filter"] == "doc_code eq 'CD 109'"
    assert args["search_text"] == QUERY and args["vector_queries"]


@pytest.mark.parametrize("mode", [Mode.VECTOR, Mode.HYBRID, Mode.SEMANTIC])
def test_vector_modes_need_a_vector(mode):
    with pytest.raises(ValueError, match="vector"):
        query_args(QUERY, None, mode, top=5, odata_filter=None)


@pytest.mark.parametrize(("query", "top"), [("  ", 5), (QUERY, 0)])
def test_bad_query_or_top_is_rejected(query, top):
    with pytest.raises(ValueError):
        query_args(query, vector(), Mode.HYBRID, top=top, odata_filter=None)


class FakeSearchClient:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.kwargs: dict = {}

    def search(self, search_text=None, **kwargs):
        self.kwargs = {"search_text": search_text, **kwargs}
        return iter(self.docs)


def _doc(chunk_id: str, score: float, reranker: float | None = None) -> dict:
    doc = {"id": chunk_id, "citation_id": chunk_id.split("#")[0], "@search.score": score}
    return doc | ({"@search.reranker_score": reranker} if reranker is not None else {})


def test_search_returns_hits_and_stops_at_top():
    client = FakeSearchClient(
        [_doc("CD109-5.3.1#2", 0.9, 3.1), _doc("CD109-5.3", 0.8), _doc("x", 0.1)]
    )

    hits = search(client, QUERY, vector(), Mode.SEMANTIC, top=2)

    assert hits == [
        Hit("CD109-5.3.1#2", "CD109-5.3.1", 0.9, 3.1),
        Hit("CD109-5.3", "CD109-5.3", 0.8, None),
    ]
    assert client.kwargs["top"] == 2
    assert client.kwargs["select"] == ["id", "citation_id"]


@pytest.mark.parametrize("mode", list(Mode))
def test_query_args_match_the_real_sdk_signature(mode):
    # The fake client accepts anything, so bind against the SDK to catch a misspelt keyword.
    args = query_args(QUERY, vector(), mode, top=5, odata_filter=None)

    inspect.signature(SearchClient.search).bind(None, **args)


def test_extra_fields_are_selected_and_returned_on_each_hit():
    doc = _doc("CD109-5.1", 0.9) | {"text": "Gradients shall not exceed...", "page": 24}
    client = FakeSearchClient([doc])

    (hit,) = search(client, QUERY, vector(), Mode.SEMANTIC, top=1, select=("text", "page", "id"))

    assert client.kwargs["select"] == ["id", "citation_id", "text", "page"]
    assert hit.fields == {"text": "Gradients shall not exceed...", "page": 24, "id": "CD109-5.1"}
