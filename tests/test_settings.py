import pytest

from core.settings import SettingsError, load_settings

VALID = {
    "AZURE_AI_PROJECT_ENDPOINT": "https://fdy-x.services.ai.azure.com/api/projects/p",
    "CHAT_DEPLOYMENT": "gpt-5.4-mini",
    "EMBEDDING_DEPLOYMENT": "text-embedding-3-small",
    "AZURE_SEARCH_ENDPOINT": "https://srch-x.search.windows.net",
    "AZURE_SEARCH_INDEX": "dmrb-clauses",
    "AZURE_DOCINTEL_ENDPOINT": "https://di-x.cognitiveservices.azure.com/",
}


def test_loads_valid_settings():
    settings = load_settings(VALID)

    assert settings.chat_deployment == "gpt-5.4-mini"
    assert settings.search_index == "dmrb-clauses"


def test_strips_trailing_slash_so_sdk_paths_join_cleanly():
    # get_openai_client() appends "/openai/v1"; a trailing slash would give "//openai/v1".
    settings = load_settings(
        {**VALID, "AZURE_AI_PROJECT_ENDPOINT": VALID["AZURE_AI_PROJECT_ENDPOINT"] + "/"}
    )

    assert settings.project_endpoint.endswith("/api/projects/p")
    assert settings.docintel_endpoint == "https://di-x.cognitiveservices.azure.com"


def test_missing_variables_are_listed_by_name():
    env = {k: v for k, v in VALID.items() if k not in ("CHAT_DEPLOYMENT", "AZURE_SEARCH_INDEX")}

    with pytest.raises(SettingsError, match="CHAT_DEPLOYMENT, AZURE_SEARCH_INDEX"):
        load_settings(env)


def test_blank_values_count_as_missing():
    with pytest.raises(SettingsError, match="CHAT_DEPLOYMENT"):
        load_settings({**VALID, "CHAT_DEPLOYMENT": "   "})


@pytest.mark.parametrize(
    "bad",
    ["https://<search-name>.search.windows.net", "not a url", "http://srch-x.search.windows.net"],
)
def test_rejects_placeholders_non_urls_and_plain_http(bad):
    with pytest.raises(SettingsError, match="AZURE_SEARCH_ENDPOINT"):
        load_settings({**VALID, "AZURE_SEARCH_ENDPOINT": bad})


def test_errors_never_echo_the_value():
    # Values come from .env; error text may end up in logs or CI output.
    secret_looking = "https://<do-not-print-me>.search.windows.net"

    with pytest.raises(SettingsError) as err:
        load_settings({**VALID, "AZURE_SEARCH_ENDPOINT": secret_looking})

    assert "do-not-print-me" not in str(err.value)


def test_derives_resource_level_openai_endpoint_from_project_endpoint():
    settings = load_settings(VALID)

    assert settings.openai_v1_endpoint == "https://fdy-x.services.ai.azure.com/openai/v1"


@pytest.mark.parametrize(
    "bad",
    [
        "https://user:pw@srch-x.search.windows.net",
        "https://srch-x.search.windows.net:8443",
        "https://srch-x.search.windows.net/?api-key=abc",
        "https://srch-x.search.windows.net/#frag",
        "https://example.com",
    ],
)
def test_rejects_credentials_ports_queries_and_foreign_hosts(bad):
    with pytest.raises(SettingsError, match="AZURE_SEARCH_ENDPOINT"):
        load_settings({**VALID, "AZURE_SEARCH_ENDPOINT": bad})


def test_project_endpoint_must_be_a_foundry_host():
    # Embeddings send a Cognitive Services token to this host, so it must be Azure's.
    with pytest.raises(SettingsError, match="services.ai.azure.com"):
        load_settings({**VALID, "AZURE_AI_PROJECT_ENDPOINT": "https://evil.example/api/projects/p"})


def test_placeholder_in_path_is_rejected():
    with pytest.raises(SettingsError, match="placeholder"):
        load_settings(
            {
                **VALID,
                "AZURE_AI_PROJECT_ENDPOINT": "https://fdy-x.services.ai.azure.com/api/projects/<name>",
            }
        )


@pytest.mark.parametrize("bad", ["<chat>", "my deploy", "a/b", "x\ny"])
def test_rejects_placeholder_or_malformed_names(bad):
    with pytest.raises(SettingsError, match="CHAT_DEPLOYMENT has invalid characters"):
        load_settings({**VALID, "CHAT_DEPLOYMENT": bad})


def test_rejected_value_is_not_kept_on_the_exception():
    with pytest.raises(SettingsError) as err:
        load_settings({**VALID, "AZURE_SEARCH_ENDPOINT": "https://<hidden>.search.windows.net"})

    assert err.value.__context__ is None
    assert err.value.__cause__ is None


def test_every_problem_is_reported_at_once():
    with pytest.raises(SettingsError, match="AZURE_SEARCH_ENDPOINT.*CHAT_DEPLOYMENT"):
        load_settings({**VALID, "AZURE_SEARCH_ENDPOINT": "nope", "CHAT_DEPLOYMENT": "<x>"})


def test_hostnames_for_redaction():
    assert load_settings(VALID).hostnames == (
        "fdy-x.services.ai.azure.com",
        "srch-x.search.windows.net",
        "di-x.cognitiveservices.azure.com",
    )
