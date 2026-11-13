"""Shared test setup."""

import pytest

from app import review_api
from review.queue import ReviewQueue


@pytest.fixture(autouse=True)
def review_queue(tmp_path, monkeypatch) -> ReviewQueue:
    """Every test gets its own empty review queue. Without this, any test that produces a
    flagged answer through the API, the demo or the MCP server would write to the real
    data/reviews.sqlite."""
    queue = ReviewQueue(tmp_path / "reviews.sqlite")
    monkeypatch.setattr(review_api, "_QUEUE", queue)
    # For tests that start the app in a subprocess, which reads the path at import.
    monkeypatch.setenv("REVIEW_DB", str(tmp_path / "reviews.sqlite"))
    return queue
