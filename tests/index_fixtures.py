"""Shared chunks and fakes for index tests. Text: CD 109 Rev 1, © Crown copyright, OGL v3.0."""

from types import SimpleNamespace

from core.clients import EMBEDDING_DIMENSIONS
from ingest.models import Chunk


def make_chunk(chunk_id: str = "CD109-5.3.1", **overrides) -> Chunk:
    citation_id = chunk_id.split("#", 1)[0]
    fields = {
        "id": chunk_id,
        "citation_id": citation_id,
        "doc_code": "CD 109",
        "doc_title": "Highway link design",
        "version": "Revision 1",
        "label": citation_id.split("-", 1)[1],
        "kind": "clause",
        "clause_level": "advice",
        "requirement_type": "should",
        "heading_path": "5. Vertical alignment > Gradients",
        "page": 20,
        "text": "The desirable maximum gradient for motorways should be 3%.",
        "url": "https://www.standardsforhighways.co.uk/cd109.pdf#page=20",
        "refs": ("CD109-Table-5.1",),
    }
    return Chunk(**(fields | overrides))


def vector(value: float = 0.1) -> list[float]:
    return [value] * EMBEDDING_DIMENSIONS


class FakeEmbeddings:
    """Stands in for OpenAI().embeddings; records each batch it was asked for."""

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS, reverse: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.dimensions = dimensions
        self.reverse = reverse

    def create(self, *, model: str, input: list[str]):  # noqa: A002
        self.calls.append(input)
        # Encode the text's length in the vector, so tests can tell which text it belongs to.
        data = [
            SimpleNamespace(index=i, embedding=[float(len(t))] * self.dimensions)
            for i, t in enumerate(input)
        ]
        return SimpleNamespace(data=data[::-1] if self.reverse else data)
