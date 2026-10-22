"""Batch text embedding, checked so a short or reordered response cannot misalign vectors."""

from collections.abc import Sequence
from typing import Protocol

from core.clients import EMBEDDING_DIMENSIONS

BATCH_SIZE = 100  # well under the API's 2048-input limit and the deployment's per-minute tokens

Vector = list[float]


class EmbeddingsAPI(Protocol):
    def create(self, *, model: str, input: list[str]): ...  # noqa: A002 - SDK's parameter name


def embed_texts(
    embeddings: EmbeddingsAPI, model: str, texts: Sequence[str], batch_size: int = BATCH_SIZE
) -> list[Vector]:
    """One vector per text, in input order. `embeddings` is `OpenAI(...).embeddings`."""
    if blank := [i for i, t in enumerate(texts) if not t.strip()]:
        raise ValueError(f"cannot embed empty text (positions {blank[:5]})")
    vectors: list[Vector] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        data = embeddings.create(model=model, input=batch).data
        if len(data) != len(batch):
            raise RuntimeError(f"asked for {len(batch)} embeddings, got {len(data)}")
        # The API returns an `index` per item; sort on it rather than trusting response order,
        # and insist on exactly 0..n-1 so a duplicate can't shift every later vector by one.
        items = sorted(data, key=lambda d: d.index)
        if [d.index for d in items] != list(range(len(batch))):
            raise RuntimeError(f"embedding indices for texts {start}+ are not 0..{len(batch) - 1}")
        for item in items:
            if len(item.embedding) != EMBEDDING_DIMENSIONS:
                raise RuntimeError(
                    f"text {start + item.index}: expected {EMBEDDING_DIMENSIONS} dimensions, "
                    f"got {len(item.embedding)}"
                )
            vectors.append(list(item.embedding))
    return vectors
