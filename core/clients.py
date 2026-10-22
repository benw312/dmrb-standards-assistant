"""Azure clients built from Settings, so every module authenticates the same keyless way."""

from azure.core.credentials import TokenCredential
from openai import OpenAI

from core.settings import Settings

COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small; the index schema depends on it
# The openai client already backs off on 429s; the deployment's TPM limit makes them likely
# during a full re-embed, so allow a few more attempts than the default of 2.
OPENAI_MAX_RETRIES = 6


def embedding_client(settings: Settings, credential: TokenCredential) -> OpenAI:
    """OpenAI client for embeddings, on the resource-level endpoint (see Settings)."""
    from azure.identity import get_bearer_token_provider

    return OpenAI(
        base_url=settings.openai_v1_endpoint,
        api_key=get_bearer_token_provider(credential, COGNITIVE_SERVICES_SCOPE),
        max_retries=OPENAI_MAX_RETRIES,
    )
