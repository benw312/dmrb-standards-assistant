"""Currency check against standardsforhighways.co.uk, with a fake API. No network."""

import urllib.parse
from datetime import date

import pytest

from ingest.currency import CurrencyError, Status, check, main, search_url
from ingest.sources import Source

ISSUED_2020 = 1585612800000  # 2020-03-31, as the API sends it (ms)
ISSUED_2027 = 1798761600000  # 2027-01-01


def _source(version="Revision 1") -> Source:
    return Source(
        code="CD 109",
        title="Highway link design",
        version=version,
        revision_date=date(2020, 3, 1),
        url="https://www.standardsforhighways.co.uk/search/x",
        filename="cd109.pdf",
    )


def _row(revision, issued=ISSUED_2020, withdrawn=None, ref="CD 109"):
    return {
        "resultData": {
            "DOCUMENT_REFERENCE": ref,
            "REVISION": revision,
            "DATE_OF_ISSUE": issued,
            "DATE_OF_WITHDRAWAL": withdrawn,
        }
    }


def _api(*rows):
    return lambda url: {"page": list(rows)}


def test_current_when_the_latest_matches_ignoring_revision_and_version_words():
    status = check(_source("Revision 1"), _api(_row("1")))
    assert status.current and status.line() == "CD 109: current (Revision 1)"


def test_a_newer_revision_is_superseded():
    status = check(_source(), _api(_row("1"), _row("2", issued=ISSUED_2027)))
    assert not status.current and "SUPERSEDED: latest is 2" in status.line()


def test_withdrawn_is_not_current():
    status = check(_source(), _api(_row("1", withdrawn=ISSUED_2027)))
    assert not status.current and "WITHDRAWN on 2027-01-01" in status.line()


def test_other_documents_matching_the_search_are_ignored():
    status = check(_source(), _api(_row("9", issued=ISSUED_2027, ref="CD 109 NAA"), _row("1")))
    assert status.current


def test_not_found_is_not_current():
    assert not check(_source(), _api()).current


def test_an_unknown_response_shape_is_an_error_not_all_clear():
    with pytest.raises(CurrencyError, match="unexpected API response"):
        check(_source(), lambda url: {"results": []})


def test_the_query_asks_for_the_exact_reference():
    query = urllib.parse.parse_qs(urllib.parse.urlparse(search_url("CD 109")).query)
    assert query["query"] == ["(DOCUMENT_REFERENCE ~ '\"CD 109\"')"]


def test_exit_code_is_1_when_anything_is_out_of_date(capsys):
    assert main([], fetch=_api()) == 1  # nothing found for any source
    assert "NOT FOUND" in capsys.readouterr().out


def test_exit_code_is_2_when_the_site_cant_be_asked(capsys):
    def down(url):
        raise CurrencyError("couldn't query")

    assert main([], fetch=down) == 2
    assert "couldn't query" in capsys.readouterr().err


def test_status_line_for_each_case():
    assert Status("X", "1", None, None).line().endswith("NOT FOUND on standardsforhighways.co.uk")


def test_exit_code_is_0_when_everything_is_current(monkeypatch, capsys):
    import ingest.currency as currency

    monkeypatch.setattr(currency, "load_sources", lambda: [_source("Revision 1")])
    assert main([], fetch=_api(_row("1 "))) == 0  # the API pads some revisions
    assert "current" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [("Version 2.1.0", "2.1.0"), ("version 2.1.0", " 2.1.0 "), ("Revision 1", "1")],
)
def test_version_words_case_and_spacing_are_ignored(ours, theirs):
    assert check(_source(ours), _api(_row(theirs))).current


def test_a_different_version_number_is_not_current():
    assert not check(_source("Version 2.1.0"), _api(_row("2.1.1"))).current


@pytest.mark.parametrize("bad", [{"page": None}, {"page": {"a": 1}}, [1, 2]])
def test_malformed_pages_are_errors(bad):
    with pytest.raises(CurrencyError):
        check(_source(), lambda url: bad)


def test_a_row_without_an_issue_date_is_an_error_not_a_guess():
    row = _row("1")
    row["resultData"]["DATE_OF_ISSUE"] = None
    with pytest.raises(CurrencyError, match="DATE_OF_ISSUE"):
        check(_source(), _api(row, _row("2", issued=ISSUED_2027)))


def test_http_get_retries_once_then_reports_the_failure(monkeypatch):
    import http.client

    import ingest.currency as currency

    calls = []

    def flaky(request, timeout):
        calls.append(1)
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(currency.urllib.request, "urlopen", flaky)
    with pytest.raises(CurrencyError, match="couldn't query"):
        currency.http_get("https://example.org", wait_s=0)
    assert len(calls) == 2
