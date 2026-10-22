"""Smoke test: one cheap, real call to each Azure service the project uses.

    uv run python -m core.smoke_test

Checks the chat model, the embedding model, AI Search and Document Intelligence with your
`az login` identity. Costs a fraction of a penny. Prints PASS/FAIL per service and exits
non-zero if anything fails. Endpoint hostnames are redacted from any error text.
"""

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from azure.core.credentials import TokenCredential

from core.clients import EMBEDDING_DIMENSIONS, embedding_client
from core.settings import Settings, SettingsError, load_settings

MAX_DETAIL_CHARS = 300

Check = tuple[str, Callable[[], str]]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _redact(text: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "<endpoint>")
    return text


def run_checks(checks: Sequence[Check], redact: Sequence[str] = ()) -> list[CheckResult]:
    """Run every check, even after a failure, so one run shows everything that's broken.

    SDK errors often quote the request URL, so any string in `redact` is masked.
    """
    results = []
    for name, check in checks:
        try:
            results.append(CheckResult(name, True, check()))
        # Deliberately broad: a smoke test reports any failure (auth, network, quota, config)
        # rather than stopping at the first one.
        except Exception as err:  # noqa: BLE001
            message = _redact(str(err).strip(), redact)
            first_line = message.splitlines()[0] if message else ""
            results.append(
                CheckResult(name, False, f"{type(err).__name__}: {first_line}"[:MAX_DETAIL_CHARS])
            )
    return results


def summarise(results: Sequence[CheckResult]) -> tuple[str, int]:
    lines = [f"{'PASS' if r.ok else 'FAIL'}  {r.name:<24} {r.detail}" for r in results]
    failed = sum(not r.ok for r in results)
    lines.append(f"\n{len(results) - failed}/{len(results)} checks passed")
    return "\n".join(lines), 1 if failed else 0


# --- Live checks (exercised by running the smoke test, not by unit tests) ---------------------


def _check_chat(settings: Settings, credential: TokenCredential) -> str:  # pragma: no cover
    from azure.ai.projects import AIProjectClient

    client = AIProjectClient(endpoint=settings.project_endpoint, credential=credential)
    response = client.get_openai_client().responses.create(
        model=settings.chat_deployment, input="Reply with the single word: ready"
    )
    return f"{settings.chat_deployment} replied {response.output_text.strip()[:40]!r}"


def _check_embeddings(settings: Settings, credential: TokenCredential) -> str:  # pragma: no cover
    result = embedding_client(settings, credential).embeddings.create(
        model=settings.embedding_deployment, input="superelevation on a bend"
    )
    dimensions = len(result.data[0].embedding)
    if dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(f"expected {EMBEDDING_DIMENSIONS} dimensions, got {dimensions}")
    return f"{settings.embedding_deployment} returned {dimensions} dimensions"


def _check_search(settings: Settings, credential: TokenCredential) -> str:  # pragma: no cover
    from azure.search.documents.indexes import SearchIndexClient

    client = SearchIndexClient(endpoint=settings.search_endpoint, credential=credential)
    names = list(client.list_index_names())
    return f"reachable, {len(names)} index(es)"


def _check_docintel(settings: Settings, credential: TokenCredential) -> str:  # pragma: no cover
    from azure.ai.documentintelligence import DocumentIntelligenceAdministrationClient

    # Resource details is a free call: proves endpoint + auth without analysing a document.
    client = DocumentIntelligenceAdministrationClient(settings.docintel_endpoint, credential)
    client.get_resource_details()
    return "reachable, authenticated"


def main() -> int:  # pragma: no cover
    from azure.identity import DefaultAzureCredential

    try:
        settings = load_settings()
    except SettingsError as err:
        print(f"Configuration error: {err}")
        return 1

    credential = DefaultAzureCredential()
    checks: list[Check] = [
        ("chat model", lambda: _check_chat(settings, credential)),
        ("embedding model", lambda: _check_embeddings(settings, credential)),
        ("AI Search", lambda: _check_search(settings, credential)),
        ("Document Intelligence", lambda: _check_docintel(settings, credential)),
    ]
    text, code = summarise(run_checks(checks, redact=settings.hostnames))
    print(text)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
