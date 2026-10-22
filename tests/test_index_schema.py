import re

import pytest

from index.schema import (
    KEY_FIELD,
    SEMANTIC_CONFIG,
    VECTOR_FIELD,
    VECTOR_PROFILE,
    build_index,
    decode_key,
    embedding_text,
    encode_key,
    index_fields,
    to_document,
)
from ingest.models import Chunk
from tests.index_fixtures import make_chunk, vector

# Azure AI Search document keys: letters, digits, underscore, dash and equals sign only.
VALID_KEY = re.compile(r"^[A-Za-z0-9_=-]+$")


@pytest.mark.parametrize(
    "chunk_id", ["CD109-5.3.1", "CD123-E/1.1#2", "CD109-Table-6.25N2", "CG501-Glossary#3"]
)
def test_keys_are_valid_and_reversible(chunk_id):
    key = encode_key(chunk_id)

    assert VALID_KEY.match(key)
    assert decode_key(key) == chunk_id


def test_similar_ids_get_different_keys():
    assert encode_key("CD109-5.3.1") != encode_key("CD109-5.3.1#2")


def test_every_chunk_attribute_has_an_index_field():
    # Guards against adding a Chunk field and forgetting the index (upload would then fail).
    names = {f.name for f in index_fields()}

    assert names == set(Chunk.model_fields) | {KEY_FIELD, VECTOR_FIELD}


def test_filterable_and_searchable_fields():
    fields = {f.name: f for f in index_fields()}

    for name in ("doc_code", "citation_id", "kind", "requirement_type", "id"):
        assert fields[name].filterable, name
    assert fields["text"].searchable and fields["heading_path"].searchable
    assert fields[KEY_FIELD].key
    assert fields[VECTOR_FIELD].vector_search_dimensions == 1536
    assert fields[VECTOR_FIELD].vector_search_profile_name == VECTOR_PROFILE
    assert fields["page"].type == "Edm.Int32"
    assert fields["refs"].type == "Collection(Edm.String)"
    assert not fields[VECTOR_FIELD].retrievable


def test_index_has_semantic_and_vector_config():
    index = build_index("dmrb-clauses").as_dict()

    assert index["semantic"]["defaultConfiguration"] == SEMANTIC_CONFIG
    prioritized = index["semantic"]["configurations"][0]["prioritizedFields"]
    assert prioritized["titleField"]["fieldName"] == "heading_path"
    profile = index["vectorSearch"]["profiles"][0]
    assert profile["name"] == VECTOR_PROFILE
    assert profile["algorithm"] == index["vectorSearch"]["algorithms"][0]["name"]


def test_embedding_text_includes_heading_path():
    chunk = make_chunk()

    assert embedding_text(chunk) == (
        "5. Vertical alignment > Gradients\n\n"
        "The desirable maximum gradient for motorways should be 3%."
    )


def test_document_carries_chunk_fields_key_and_vector():
    chunk = make_chunk("CD109-5.3.1#2")

    doc = to_document(chunk, vector())

    assert doc[KEY_FIELD] == encode_key("CD109-5.3.1#2")
    assert doc["id"] == "CD109-5.3.1#2" and doc["citation_id"] == "CD109-5.3.1"
    assert doc["refs"] == ["CD109-Table-5.1"]  # a list, not a tuple, for JSON
    assert doc["requirement_type"] == "should"
    assert len(doc[VECTOR_FIELD]) == 1536


def test_document_keeps_nulls_for_tables():
    table = make_chunk("CD109-Table-5.1", kind="table", clause_level=None, requirement_type=None)

    doc = to_document(table, vector())

    assert doc["clause_level"] is None and doc["requirement_type"] is None


def test_wrong_vector_size_is_rejected():
    with pytest.raises(ValueError, match="1536"):
        to_document(make_chunk(), [0.1] * 3)
