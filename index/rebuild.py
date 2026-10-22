"""Recreate the search index from data/chunks/: embed, drop, create, upload, verify.

    uv run python -m index.rebuild

Embeds every chunk first, so a failed or throttled embedding run leaves the old index in place.
Then deletes and recreates the index (name from AZURE_SEARCH_INDEX), uploads in batches and
waits until Search reports every document. Costs well under a penny per full run
(~650 chunks x ~250 tokens of text-embedding-3-small); the free Search tier is free.
"""

import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from index.embed import Vector
from index.schema import build_index, embedding_text, to_document
from ingest.models import Chunk

UPLOAD_BATCH_SIZE = 100
COUNT_TIMEOUT_S = 60.0  # Search's document count lags uploads by a few seconds
COUNT_POLL_S = 2.0


class IndexAPI(Protocol):
    def list_index_names(self) -> Any: ...
    def delete_index(self, index: str) -> None: ...
    def create_index(self, index: Any) -> Any: ...


class DocumentsAPI(Protocol):
    def upload_documents(self, documents: list[dict]) -> Any: ...
    def get_document_count(self) -> int: ...


class RebuildError(RuntimeError):
    """The rebuild failed after the old index was deleted."""


@dataclass(frozen=True)
class RebuildReport:
    index: str
    chunks: int
    by_kind: dict[str, int]
    documents_counted: int


def upload(documents: Sequence[dict], client: DocumentsAPI, batch_size: int) -> None:
    """Upload in batches; raise naming every document Search rejected."""
    failed = []
    for start in range(0, len(documents), batch_size):
        results = client.upload_documents(list(documents[start : start + batch_size]))
        # A batch call can succeed overall while rejecting individual documents.
        failed += [f"{r.key}: {r.error_message}" for r in results if not r.succeeded]
    if failed:
        raise RuntimeError(f"{len(failed)} documents rejected: {'; '.join(failed[:5])}")


def wait_for_count(
    client: DocumentsAPI,
    expected: int,
    timeout_s: float = COUNT_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    waited = 0.0
    while (count := client.get_document_count()) != expected:
        if waited >= timeout_s:
            raise RuntimeError(
                f"index holds {count} documents after {timeout_s}s, expected {expected}"
            )
        sleep(COUNT_POLL_S)
        waited += COUNT_POLL_S
    return count


def rebuild(
    chunks: Sequence[Chunk],
    embed: Callable[[Sequence[str]], list[Vector]],
    index_client: IndexAPI,
    documents_client: DocumentsAPI,
    index_name: str,
    batch_size: int = UPLOAD_BATCH_SIZE,
    sleep: Callable[[float], None] = time.sleep,
) -> RebuildReport:
    if not chunks:
        raise ValueError("no chunks to index: run python -m ingest.chunk first")
    vectors = embed([embedding_text(c) for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    documents = [to_document(c, v) for c, v in zip(chunks, vectors, strict=True)]

    # Recreate rather than update: a schema change or a removed chunk must not leave stale
    # fields or documents behind.
    if index_name in set(index_client.list_index_names()):
        index_client.delete_index(index_name)
    try:
        index_client.create_index(build_index(index_name))
        upload(documents, documents_client, batch_size)
        counted = wait_for_count(documents_client, len(documents), sleep=sleep)
    except Exception as err:
        # The old index is already gone, so say so: searches now see an empty or partial index.
        raise RebuildError(
            f"index {index_name!r} is missing or partial after: {err}. "
            "Re-run python -m index.rebuild"
        ) from err
    return RebuildReport(index_name, len(chunks), dict(Counter(c.kind for c in chunks)), counted)


def main() -> int:  # pragma: no cover - wires live Azure clients
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    from core.clients import embedding_client
    from core.settings import SettingsError, load_settings
    from index.embed import embed_texts
    from ingest.chunk import read_chunks

    try:
        settings = load_settings()
    except SettingsError as err:
        print(f"Configuration error: {err}")
        return 1
    credential = DefaultAzureCredential()
    embeddings = embedding_client(settings, credential).embeddings
    chunks = read_chunks()
    print(f"Embedding {len(chunks)} chunks with {settings.embedding_deployment}...")
    report = rebuild(
        chunks,
        lambda texts: embed_texts(embeddings, settings.embedding_deployment, texts),
        SearchIndexClient(endpoint=settings.search_endpoint, credential=credential),
        SearchClient(settings.search_endpoint, settings.search_index, credential=credential),
        settings.search_index,
    )
    kinds = ", ".join(f"{n} {k}" for k, n in sorted(report.by_kind.items()))
    print(f"Index {report.index!r}: {report.documents_counted} documents ({kinds})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
