"""Are the indexed standards still current? Asks standardsforhighways.co.uk.

    uv run python -m ingest.currency

For each entry in data/sources.yaml, looks the document up on the DMRB site's search API and
reports whether the version we index is still the latest and not withdrawn. Exit code 1 if
anything is out of date, so it can run on a schedule (e.g. a weekly CI job) and fail loudly:
an assistant citing a superseded standard is worse than one that refuses.

The API is the one the site's own pages call. It isn't a published, versioned API, so a
change to it shows up here as an error to look at, never as "all current".
"""

import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ingest.sources import Source, load_sources

SEARCH_URL = "https://www.standardsforhighways.co.uk/tses/v2/search"
COLUMNS = "NODE_ID,DOCUMENT_REFERENCE,TITLE,REVISION,DATE_OF_ISSUE,DATE_OF_WITHDRAWAL"
TIMEOUT_S = 30

# GET url -> parsed JSON. Injectable so tests need no network.
Fetch = Callable[[str], dict]


class CurrencyError(RuntimeError):
    """The site couldn't be asked, or answered in a shape this code doesn't know."""


@dataclass(frozen=True)
class Status:
    code: str
    ours: str
    latest: str | None
    withdrawn_on: str | None

    @property
    def current(self) -> bool:
        return (
            self.latest is not None
            and _bare(self.ours) == _bare(self.latest)
            and (self.withdrawn_on is None)
        )

    def line(self) -> str:
        if self.latest is None:
            return f"{self.code}: NOT FOUND on standardsforhighways.co.uk"
        if self.withdrawn_on:
            return f"{self.code}: WITHDRAWN on {self.withdrawn_on} (we index {self.ours})"
        if not self.current:
            return f"{self.code}: SUPERSEDED: latest is {self.latest}, we index {self.ours}"
        return f"{self.code}: current ({self.ours})"


def _bare(version: str) -> str:
    """'Revision 1' and '1', 'Version 2.1.0' and '2.1.0' are the same version."""
    text = version.strip().lower()
    for prefix in ("revision", "version"):
        text = text.removeprefix(prefix)
    return text.strip()


def _date(ms: int | None) -> str | None:
    return None if not ms else datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def http_get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return json.load(response)
    except (OSError, ValueError) as err:
        raise CurrencyError(f"couldn't query standardsforhighways.co.uk: {err}") from err


def search_url(code: str) -> str:
    params = {
        "query": f"(DOCUMENT_REFERENCE ~ '\"{code}\"')",
        "columns": COLUMNS,
        "matchType": "ALL",
        "sortColumn": "DATE_OF_ISSUE",
        "sortDirection": "DESC",
    }
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def check(source: Source, fetch: Fetch = http_get) -> Status:
    data = fetch(search_url(source.code))
    try:
        rows = [r["resultData"] for r in data["page"]]
        # The search matches on the reference, so "CD 109" can also return "CD 109 NAA".
        exact = [r for r in rows if r["DOCUMENT_REFERENCE"].strip() == source.code]
    except (KeyError, TypeError, AttributeError) as err:
        raise CurrencyError(f"{source.code}: unexpected API response ({err!r})") from err
    if not exact:
        return Status(source.code, source.version, None, None)
    latest = max(exact, key=lambda r: r.get("DATE_OF_ISSUE") or 0)
    return Status(
        source.code,
        source.version,
        str(latest["REVISION"]).strip(),
        _date(latest.get("DATE_OF_WITHDRAWAL")),
    )


def main(argv: Sequence[str] | None = None, fetch: Fetch = http_get) -> int:
    del argv  # no options yet
    try:
        statuses = [check(source, fetch) for source in load_sources()]
    except CurrencyError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    for status in statuses:
        print(status.line())
    return 0 if all(s.current for s in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
