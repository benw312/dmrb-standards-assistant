from types import SimpleNamespace

import pytest

from index.embed import embed_texts
from tests.index_fixtures import FakeEmbeddings


def test_texts_are_embedded_in_batches_in_order():
    fake = FakeEmbeddings()
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]

    vectors = embed_texts(fake, "text-embedding-3-small", texts, batch_size=2)

    assert [len(b) for b in fake.calls] == [2, 2, 1]
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_response_order_is_restored_from_the_index_field():
    vectors = embed_texts(FakeEmbeddings(reverse=True), "m", ["a", "bb", "ccc"])

    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]


def test_wrong_dimensions_raise():
    with pytest.raises(RuntimeError, match="1536 dimensions"):
        embed_texts(FakeEmbeddings(dimensions=3), "m", ["a"])


def test_short_response_raises():
    class Short:
        def create(self, *, model, input):  # noqa: A002
            return SimpleNamespace(data=[])

    with pytest.raises(RuntimeError, match="asked for 1"):
        embed_texts(Short(), "m", ["a"])


def test_empty_text_is_refused_before_any_call():
    fake = FakeEmbeddings()

    with pytest.raises(ValueError, match="empty"):
        embed_texts(fake, "m", ["fine", "  "])
    assert fake.calls == []


def test_duplicate_response_indices_raise():
    class Duplicated:
        def create(self, *, model, input):  # noqa: A002
            item = SimpleNamespace(index=0, embedding=[0.0] * 1536)
            return SimpleNamespace(data=[item, item])

    with pytest.raises(RuntimeError, match="not 0..1"):
        embed_texts(Duplicated(), "m", ["a", "b"])
