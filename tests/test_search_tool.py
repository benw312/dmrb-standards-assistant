"""search_standards: argument checks and the table -> referring-clause expansion."""

import pytest
from pydantic import ValidationError

from agent.tools.search_tool import MAX_REFERRING, SearchStandardsArgs, search_standards


def _doc(chunk_id: str, kind: str = "clause") -> dict:
    return {
        "id": chunk_id,
        "citation_id": chunk_id.split("#", 1)[0],
        "@search.score": 1.0,
        "kind": kind,
        "text": f"text of {chunk_id}",
    }


class FakeSearch:
    """Referring-clause lookups get `referring[table]`; every other query gets `main`."""

    def __init__(self, main: list[dict], referring: dict[str, list[dict]] | None = None):
        self.main, self.referring, self.calls = main, referring or {}, []

    def search(self, search_text=None, **kwargs):
        self.calls.append({"search_text": search_text, **kwargs})
        if "refs/any" in (kwargs.get("filter") or ""):
            table = kwargs["filter"].split("r eq '", 1)[1].rstrip("')")
            return iter(self.referring.get(table, []))
        return iter(self.main)


def _embed(query: str) -> list[float]:
    return [0.1, 0.2]


def test_standard_is_normalised_to_the_index_form():
    assert SearchStandardsArgs(query="gradient", standard="cd109").standard == "CD 109"
    assert SearchStandardsArgs(query="gradient", standard=" CG 501 ").standard == "CG 501"


@pytest.mark.parametrize("bad", ["CD 109' or kind eq 'table", "Table 5.1", "CD1090", ""])
def test_anything_but_a_document_code_is_rejected_before_it_reaches_odata(bad):
    with pytest.raises(ValidationError):
        SearchStandardsArgs(query="gradient", standard=bad)


def test_blank_query_and_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        SearchStandardsArgs(query="   ")
    with pytest.raises(ValidationError):
        SearchStandardsArgs(query="gradient", top=50)


def test_standard_becomes_a_doc_code_filter():
    client = FakeSearch([_doc("CD109-5.1")])
    search_standards(client, _embed, query="gradient", standard="CD109")
    assert client.calls[0]["filter"] == "doc_code eq 'CD 109'"
    assert client.calls[0]["query_type"] == "semantic"


def test_a_table_brings_the_clauses_that_cite_it():
    client = FakeSearch(
        [_doc("CD109-Table-5.1", "table"), _doc("CD109-5.2")],
        {"CD109-Table-5.1": [_doc("CD109-5.1"), _doc("CD109-5.2")]},
    )
    hits = search_standards(client, _embed, query="motorway gradient")
    # 5.2 was already in the results, so only 5.1 is added, after the search hits.
    assert [h.chunk_id for h in hits] == ["CD109-Table-5.1", "CD109-5.2", "CD109-5.1"]
    lookup = client.calls[1]
    assert lookup["filter"] == "kind eq 'clause' and refs/any(r: r eq 'CD109-Table-5.1')"
    assert "vector_queries" not in lookup


def test_no_tables_means_no_extra_queries():
    client = FakeSearch([_doc("CD109-5.1"), _doc("CD109-5.2")])
    assert len(search_standards(client, _embed, query="gradient")) == 2
    assert len(client.calls) == 1


def test_expansion_is_capped():
    tables = [_doc(f"CD109-Table-{i}.1", "table") for i in range(1, 5)]
    referring = {
        f"CD109-Table-{i}.1": [_doc(f"CD109-{i}.1"), _doc(f"CD109-{i}.2")] for i in range(1, 5)
    }
    hits = search_standards(FakeSearch(tables, referring), _embed, query="tables")
    assert len(hits) == len(tables) + MAX_REFERRING


def test_an_injected_standard_never_reaches_the_index():
    client = FakeSearch([])
    with pytest.raises(ValidationError):
        search_standards(client, _embed, query="x", standard="CD 109' or kind eq 'table")
    assert client.calls == []


def test_unicode_digits_are_not_a_document_code():
    with pytest.raises(ValidationError):
        SearchStandardsArgs(query="x", standard="CD １０９")
