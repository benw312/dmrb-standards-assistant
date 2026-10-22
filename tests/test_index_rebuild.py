from types import SimpleNamespace

import pytest

from index.rebuild import RebuildError, rebuild, upload, wait_for_count
from index.schema import encode_key
from tests.index_fixtures import make_chunk, vector

CHUNKS = [
    make_chunk("CD109-5.3.1"),
    make_chunk("CD109-5.3.1#2"),
    make_chunk("CD109-Table-5.1", kind="table", clause_level=None, requirement_type=None),
]


class FakeIndexClient:
    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self.names = list(existing)
        self.events: list[str] = []

    def list_index_names(self):
        return iter(self.names)

    def delete_index(self, index):
        self.events.append(f"delete {index}")

    def create_index(self, index):
        self.events.append(f"create {index.name}")


class FakeDocumentsClient:
    def __init__(self, reject: set[str] = frozenset(), counts: list[int] | None = None) -> None:
        self.batches: list[list[dict]] = []
        self.reject = reject
        self.counts = counts

    def upload_documents(self, documents):
        self.batches.append(documents)
        return [
            SimpleNamespace(
                key=d["key"], succeeded=d["id"] not in self.reject, error_message="bad field"
            )
            for d in documents
        ]

    def get_document_count(self):
        if self.counts:
            return self.counts.pop(0)
        return sum(len(b) for b in self.batches)


def _embed(texts):
    return [vector() for _ in texts]


def test_rebuild_recreates_index_and_uploads_in_batches():
    index, docs = FakeIndexClient(existing=("dmrb-clauses",)), FakeDocumentsClient()

    report = rebuild(CHUNKS, _embed, index, docs, "dmrb-clauses", batch_size=2)

    assert index.events == ["delete dmrb-clauses", "create dmrb-clauses"]
    assert [len(b) for b in docs.batches] == [2, 1]
    assert docs.batches[0][1]["key"] == encode_key("CD109-5.3.1#2")
    assert report.documents_counted == 3
    assert report.by_kind == {"clause": 2, "table": 1}


def test_new_index_is_created_without_a_delete():
    index = FakeIndexClient()

    rebuild(CHUNKS, _embed, index, FakeDocumentsClient(), "dmrb-clauses")

    assert index.events == ["create dmrb-clauses"]


def test_failed_embedding_leaves_the_old_index_alone():
    index = FakeIndexClient(existing=("dmrb-clauses",))

    def throttled(texts):
        raise RuntimeError("429 Too Many Requests")

    with pytest.raises(RuntimeError, match="429"):
        rebuild(CHUNKS, throttled, index, FakeDocumentsClient(), "dmrb-clauses")
    assert index.events == []


def test_vector_count_mismatch_is_caught_before_touching_the_index():
    index = FakeIndexClient(existing=("dmrb-clauses",))

    with pytest.raises(RuntimeError, match="3 chunks but 2 vectors"):
        rebuild(CHUNKS, lambda t: [vector(), vector()], index, FakeDocumentsClient(), "x")
    assert index.events == []


def test_no_chunks_is_an_error():
    with pytest.raises(ValueError, match="ingest.chunk"):
        rebuild([], _embed, FakeIndexClient(), FakeDocumentsClient(), "x")


def test_rejected_documents_are_named():
    docs = [{"key": "k1", "id": "CD109-5.3.1"}, {"key": "k2", "id": "CD109-5.3.2"}]

    with pytest.raises(RuntimeError, match="1 documents rejected: k2: bad field"):
        upload(docs, FakeDocumentsClient(reject={"CD109-5.3.2"}), batch_size=100)


def test_wait_for_count_polls_until_the_count_catches_up():
    sleeps = []

    count = wait_for_count(FakeDocumentsClient(counts=[0, 2, 3]), 3, sleep=sleeps.append)

    assert count == 3 and len(sleeps) == 2


def test_wait_for_count_times_out():
    client = FakeDocumentsClient(counts=[1] * 100)

    with pytest.raises(RuntimeError, match="holds 1 documents"):
        wait_for_count(client, 3, timeout_s=4, sleep=lambda s: None)


def test_failure_after_delete_says_the_index_is_now_partial():
    index = FakeIndexClient(existing=("dmrb-clauses",))
    docs = FakeDocumentsClient(reject={"CD109-Table-5.1"})

    with pytest.raises(RebuildError, match="missing or partial.*Re-run"):
        rebuild(CHUNKS, _embed, index, docs, "dmrb-clauses")
    assert index.events == ["delete dmrb-clauses", "create dmrb-clauses"]


def test_count_timeout_inside_rebuild_is_a_rebuild_error():
    docs = FakeDocumentsClient(counts=[0] * 100)

    with pytest.raises(RebuildError, match="holds 0 documents"):
        rebuild(CHUNKS, _embed, FakeIndexClient(), docs, "x", sleep=lambda s: None)
