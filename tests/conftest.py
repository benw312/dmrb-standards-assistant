"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def golden_file(tmp_path_factory, monkeypatch):
    """Every test's golden set is a temp file holding only comments. Without this, a test of
    the golden set editor could append to the real evals/golden.yaml, which is Ben's.
    In a folder of its own: tests use tmp_path as a data folder and read every file in it."""
    path = tmp_path_factory.mktemp("golden") / "golden.yaml"
    path.write_text("# Golden set (test copy)\n", encoding="utf-8")
    monkeypatch.setenv("GOLDEN_FILE", str(path))
    return path
