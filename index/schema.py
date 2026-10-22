"""Azure AI Search index definition: one field per Chunk attribute, plus a key and a vector.

Search document keys may only contain letters, digits, '_', '-' and '=', so chunk IDs such as
'CD123-E/1.1#2' can't be keys as they are. The key is a URL-safe base64 encoding of the chunk
ID (reversible, collision-free), and the readable ID is kept in its own filterable `id` field.
"""

import base64
from collections.abc import Sequence

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from core.clients import EMBEDDING_DIMENSIONS
from ingest.models import Chunk

KEY_FIELD = "key"
VECTOR_FIELD = "embedding"
SEMANTIC_CONFIG = "dmrb-semantic"
VECTOR_PROFILE = "dmrb-vector"
HNSW_CONFIG = "dmrb-hnsw"
# Microsoft's English analyser lemmatises ("banked" ~ "bank") better than the Lucene stemmer.
TEXT_ANALYZER = "en.microsoft"

_STRING = SearchFieldDataType.STRING
_STRINGS = SearchFieldDataType.Collection(SearchFieldDataType.STRING)


def encode_key(chunk_id: str) -> str:
    """'CD123-E/1.1#2' -> a valid Search document key."""
    return base64.urlsafe_b64encode(chunk_id.encode()).decode()


def decode_key(key: str) -> str:
    return base64.urlsafe_b64decode(key.encode()).decode()


def embedding_text(chunk: Chunk) -> str:
    """What gets embedded. The heading path adds context a bare clause lacks ("Superelevation")."""
    return f"{chunk.heading_path}\n\n{chunk.text}"


def _searchable(name: str) -> SearchField:
    return SearchField(name=name, type=_STRING, searchable=True, analyzer_name=TEXT_ANALYZER)


def _filterable(name: str) -> SearchField:
    return SimpleField(name=name, type=_STRING, filterable=True, facetable=True)


def index_fields() -> list[SearchField]:
    return [
        SimpleField(name=KEY_FIELD, type=_STRING, key=True),
        SimpleField(name="id", type=_STRING, filterable=True),
        SimpleField(name="citation_id", type=_STRING, filterable=True),
        _filterable("doc_code"),
        SimpleField(name="doc_title", type=_STRING),
        SimpleField(name="version", type=_STRING, filterable=True),
        SimpleField(name="label", type=_STRING, filterable=True),
        _filterable("kind"),
        _filterable("clause_level"),
        _filterable("requirement_type"),
        _searchable("heading_path"),
        SimpleField(name="page", type=SearchFieldDataType.INT32, sortable=True),
        _searchable("text"),
        SimpleField(name="url", type=_STRING),
        SimpleField(name="refs", type=_STRINGS, filterable=True),
        SimpleField(name="warnings", type=_STRINGS),
        SearchField(
            name=VECTOR_FIELD,
            type=SearchFieldDataType.Collection(SearchFieldDataType.SINGLE),
            searchable=True,
            retrievable=False,  # 1536 floats per hit would bloat every response
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
    ]


def build_index(name: str) -> SearchIndex:
    semantic = SemanticConfiguration(
        name=SEMANTIC_CONFIG,
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="heading_path"),
            content_fields=[SemanticField(field_name="text")],
        ),
    )
    vector = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=HNSW_CONFIG)],  # cosine by default
        profiles=[
            VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name=HNSW_CONFIG)
        ],
    )
    return SearchIndex(
        name=name,
        fields=index_fields(),
        vector_search=vector,
        semantic_search=SemanticSearch(
            default_configuration_name=SEMANTIC_CONFIG, configurations=[semantic]
        ),
    )


def to_document(chunk: Chunk, vector: Sequence[float]) -> dict:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"{chunk.id}: expected {EMBEDDING_DIMENSIONS}-dim vector, got {len(vector)}"
        )
    return {
        KEY_FIELD: encode_key(chunk.id),
        **chunk.model_dump(mode="json"),  # tuples become lists, as Search expects
        VECTOR_FIELD: list(vector),
    }
