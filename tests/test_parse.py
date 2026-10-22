import json
from datetime import date
from pathlib import Path

import pytest

from ingest.parse import ParseError, check_footer, estimate_cost_usd, parse_source
from ingest.sources import Source

FOOTER = (
    "Downloaded from https://www.standardsforhighways.co.uk on 22-Oct-2026, "
    "CD 109  Revision 1, published: Mar-2020"
)
SOURCE = Source(
    code="CD 109",
    title="Highway link design",
    version="Revision 1",
    revision_date=date(2020, 3, 1),
    url="https://www.standardsforhighways.co.uk/",
    filename="CD109.pdf",
)


class FakeResult:
    content = "# 1. Scope\n\n1.1 The layout shall be designed.\n"

    def as_dict(self) -> dict:
        return {"content": self.content, "pages": [{"pageNumber": 1}]}


class FakeClient:
    """Stands in for DocumentIntelligenceClient; counts billable calls."""

    def __init__(self) -> None:
        self.calls = 0

    def begin_analyze_document(self, model_id, body, **kwargs):
        self.calls += 1
        self.model_id, self.kwargs = model_id, kwargs
        body.read()  # the real client streams the file
        return type("Poller", (), {"result": lambda _self: FakeResult()})()


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    pdf_dir, out_dir = tmp_path / "pdfs", tmp_path / "parsed"
    pdf_dir.mkdir()
    (pdf_dir / "CD109.pdf").write_bytes(b"%PDF-1.7 fake")
    return pdf_dir, out_dir


def reader(text: str = FOOTER, pages: int = 105):
    return lambda _path: (text, pages)


# --- Pre-flight footer check (free, before any billable call) -------------------------


def test_footer_matching_sources_yaml_passes():
    check_footer(FOOTER, SOURCE)


def test_wrong_version_is_caught_before_paying():
    with pytest.raises(ParseError, match="Revision 2.*sources.yaml says 'Revision 1'"):
        check_footer(FOOTER.replace("Revision 1", "Revision 2"), SOURCE)


def test_wrong_document_is_caught():
    with pytest.raises(ParseError, match="CD 123"):
        check_footer(FOOTER.replace("CD 109", "CD 123"), SOURCE)


def test_missing_footer_is_an_error():
    with pytest.raises(ParseError, match="no standardsforhighways footer"):
        check_footer("Some other PDF", SOURCE)


def test_cost_estimate_uses_layout_price():
    assert estimate_cost_usd(313) == pytest.approx(3.13)


# --- parse_source ------------------------------------------------------------------


def test_parses_and_caches_markdown_json_and_metadata(dirs):
    pdf_dir, out_dir = dirs
    client = FakeClient()

    outcome = parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())

    assert client.calls == 1
    assert client.model_id == "prebuilt-layout"
    assert client.kwargs["output_content_format"] == "markdown"
    assert outcome.cached is False and outcome.pages == 105
    assert outcome.paths.markdown.read_text().startswith("# 1. Scope")
    assert json.loads(outcome.paths.result_json.read_text())["pages"] == [{"pageNumber": 1}]
    meta = json.loads(outcome.paths.meta.read_text())
    assert meta["doc_code"] == "CD 109" and meta["version"] == "Revision 1"
    assert meta["model"] == "prebuilt-layout" and len(meta["pdf_sha256"]) == 64


def test_second_run_uses_cache_and_costs_nothing(dirs):
    pdf_dir, out_dir = dirs
    client = FakeClient()
    parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())

    outcome = parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())

    assert client.calls == 1
    assert outcome.cached is True


def test_changed_pdf_is_parsed_again(dirs):
    pdf_dir, out_dir = dirs
    client = FakeClient()
    parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())
    (pdf_dir / "CD109.pdf").write_bytes(b"%PDF-1.7 revised")

    parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())

    assert client.calls == 2


def test_force_reparses(dirs):
    pdf_dir, out_dir = dirs
    client = FakeClient()
    parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())

    parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader(), force=True)

    assert client.calls == 2


def test_version_mismatch_never_calls_the_service(dirs):
    pdf_dir, out_dir = dirs
    client = FakeClient()

    with pytest.raises(ParseError):
        parse_source(
            SOURCE, client, pdf_dir, out_dir, page_reader=reader(FOOTER.replace("1,", "2,"))
        )

    assert client.calls == 0


def test_missing_pdf_names_the_file(dirs):
    pdf_dir, out_dir = dirs

    with pytest.raises(ParseError, match="Missing.pdf"):
        parse_source(
            SOURCE.model_copy(update={"filename": "Missing.pdf"}),
            FakeClient(),
            pdf_dir,
            out_dir,
            page_reader=reader(),
        )


def test_interrupted_write_is_not_mistaken_for_a_cache(dirs, monkeypatch):
    # Metadata is written last, so a crash mid-write leaves no metadata and forces a re-parse.
    pdf_dir, out_dir = dirs
    client = FakeClient()
    outcome = parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())
    outcome.paths.meta.unlink()

    parse_source(SOURCE, client, pdf_dir, out_dir, page_reader=reader())

    assert client.calls == 2
