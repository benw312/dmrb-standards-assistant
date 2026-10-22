"""Runtime settings, read from environment variables (a local .env in development).

Only endpoints and deployment names live here: authentication is DefaultAzureCredential.
Errors name the variable that's wrong but never echo its value, because values come from
.env and error text can end up in logs or CI output.
"""

import os
import re
from collections.abc import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, HttpUrl, TypeAdapter, ValidationError

_URL = TypeAdapter(HttpUrl)

# Settings field -> (environment variable, required host suffix). Pinning each endpoint to its
# Azure domain catches typos early and means a bearer token is never sent to some other host.
_URL_VARS = {
    "project_endpoint": ("AZURE_AI_PROJECT_ENDPOINT", ".services.ai.azure.com"),
    "search_endpoint": ("AZURE_SEARCH_ENDPOINT", ".search.windows.net"),
    "docintel_endpoint": ("AZURE_DOCINTEL_ENDPOINT", ".cognitiveservices.azure.com"),
}
_NAME_VARS = {
    "chat_deployment": "CHAT_DEPLOYMENT",
    "embedding_deployment": "EMBEDDING_DEPLOYMENT",
    "search_index": "AZURE_SEARCH_INDEX",
}
# Azure deployment and index names: letters, digits, '.', '_', '-'. Rejects "<placeholder>".
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class SettingsError(RuntimeError):
    """Configuration is missing or malformed."""


def _host(url: str) -> str:
    """'https://a.b.c/x/y' -> 'a.b.c'. Only used on URLs that passed _url_problem."""
    return url.split("//", 1)[1].split("/", 1)[0]


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_endpoint: str
    chat_deployment: str
    embedding_deployment: str
    search_endpoint: str
    search_index: str
    docintel_endpoint: str

    @property
    def openai_v1_endpoint(self) -> str:
        """Resource-level OpenAI-compatible endpoint of the Foundry resource.

        The project endpoint's /openai/v1 serves chat but returns 404 for embeddings (seen
        Oct 2026, even on the default project), so embeddings go through this one instead.
        """
        return f"https://{_host(self.project_endpoint)}/openai/v1"

    @property
    def hostnames(self) -> tuple[str, ...]:
        """Endpoint hosts, so output can redact them."""
        return tuple(
            _host(u) for u in (self.project_endpoint, self.search_endpoint, self.docintel_endpoint)
        )


def _url_problem(raw: str, host_suffix: str) -> str | None:
    """Why `raw` isn't an acceptable endpoint, or None. Never includes the value."""
    try:
        url = _URL.validate_python(raw)
    except ValidationError:
        return "is not a valid URL"
    if url.scheme != "https" or "<" in raw:
        return "must be a real https:// endpoint, not a placeholder"
    # '@' means credentials, ':' an explicit port; neither belongs in these endpoints.
    if "@" in _host(raw) or ":" in _host(raw) or url.query or url.fragment:
        return "must not contain credentials, a port, a query string or a fragment"
    if not (url.host or "").endswith(host_suffix):
        return f"must be an Azure endpoint ending in {host_suffix}"
    return None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read settings from `environ`, or from os.environ after loading .env if not given."""
    if environ is None:
        load_dotenv(override=False)  # real environment variables win over .env
        environ = os.environ

    urls = {f: environ.get(var, "").strip() for f, (var, _) in _URL_VARS.items()}
    names = {f: environ.get(var, "").strip() for f, var in _NAME_VARS.items()}
    var_of = {f: var for f, (var, _) in _URL_VARS.items()} | _NAME_VARS

    missing = [var_of[f] for f, v in (urls | names).items() if not v]
    if missing:
        raise SettingsError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )
    # Problems are collected rather than raised inside an `except`, so no exception carries
    # the offending value along as its __context__.
    problems = [
        f"{var} {problem}"
        for f, (var, suffix) in _URL_VARS.items()
        if (problem := _url_problem(urls[f], suffix))
    ] + [
        f"{var} has invalid characters"
        for f, var in _NAME_VARS.items()
        if not _NAME.fullmatch(names[f])
    ]
    if problems:
        raise SettingsError("; ".join(problems))
    # SDKs append paths ("/openai/v1"); a trailing slash would double it.
    return Settings(**{f: v.rstrip("/") for f, v in urls.items()}, **names)
