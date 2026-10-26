"""REST API with a fake answer engine: validation, response shape, flags and error mapping."""

import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from azure.core.exceptions import ServiceRequestError
from fastapi.testclient import TestClient
from openai import APIConnectionError

from agent.citations import verify_citations
from agent.loop import AgentResult
from agent.rag import DISCLAIMER, RagError, RagResult
from app import api
from app.api import create_app, get_answerer
from core.settings import SettingsError
from tests.test_rag import GOOD


def _agent_result(**overrides) -> AgentResult:
    fields = {
        "question": "Max motorway gradient?",
        "answer": GOOD,
        "check": None,
        "evidence": ("CD109-5.1", "CD109-Table-5.1"),
        "tool_calls": ({"name": "search_standards", "arguments": {}, "ok": True, "seconds": 0.1},),
        "steps": 2,
        "input_tokens": 800,
        "output_tokens": 90,
        "seconds": 4.2,
        "trace_id": "20261025-000000-abc123",
    }
    return AgentResult(**(fields | overrides))


class FakeAnswerer:
    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error, self.calls = result, error, []

    def __call__(self, question, mode):
        self.calls.append((question, mode))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def make_client():
    app = create_app()

    def make(answerer) -> TestClient:
        app.dependency_overrides[get_answerer] = lambda: answerer
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_health_needs_no_azure(make_client):
    assert make_client(FakeAnswerer()).get("/health").json() == {"status": "ok"}


def test_ask_returns_a_cited_answer(make_client):
    answerer = FakeAnswerer(_agent_result())
    body = make_client(answerer).post("/ask", json={"question": "  Max motorway gradient? "})

    assert body.status_code == 200
    data = body.json()
    assert answerer.calls == [("Max motorway gradient?", "agent")]  # trimmed, default mode
    assert data["answer"].endswith(DISCLAIMER)
    assert data["citations"] == [
        {"id": "CD109-5.1", "cite": "[CD 109 cl. 5.1]"},
        {"id": "CD109-Table-5.1", "cite": "[CD 109 Table 5.1]"},
    ]
    assert data["needs_human_review"] is False and data["escalation"] == []
    assert data["tools_used"] == ["search_standards"]
    assert data["trace_id"] == "20261025-000000-abc123"


def test_an_escalated_answer_is_still_a_200_with_the_flag(make_client):
    result = _agent_result(escalation=("Stopped at the 6-step limit without a checked answer.",))
    data = make_client(FakeAnswerer(result)).post("/ask", json={"question": "q"}).json()
    assert data["needs_human_review"] is True and "6-step limit" in data["escalation"][0]


def test_rag_mode_has_no_trace(make_client):
    retrieved = ("CD109-5.1", "CD109-Table-5.1")
    check = verify_citations(GOOD, retrieved)
    result = RagResult(question="q", answer=GOOD, retrieved=retrieved, check=check, attempts=1)
    answerer = FakeAnswerer(result)
    data = make_client(answerer).post("/ask", json={"question": "q", "mode": "rag"}).json()
    assert answerer.calls == [("q", "rag")]
    assert data["mode"] == "rag" and data["trace_id"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 1001},
        {"question": "q", "mode": "magic"},
        {"question": "q", "extra": 1},
        {},
    ],
)
def test_bad_requests_are_rejected_before_any_model_call(make_client, body):
    answerer = FakeAnswerer(_agent_result())
    assert make_client(answerer).post("/ask", json=body).status_code == 422
    assert answerer.calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ServiceRequestError("https://srch-secret-name.search.windows.net unreachable"), 502),
        (APIConnectionError(request=httpx.Request("POST", "https://srch-secret-name")), 502),
        (KeyError("srch-secret-name"), 500),
    ],
)
def test_failures_return_a_request_id_and_no_details(make_client, caplog, error, status):
    response = make_client(FakeAnswerer(error=error)).post("/ask", json={"question": "q"})
    body = response.json()
    assert response.status_code == status
    assert set(body) == {"detail", "request_id"} and len(body["request_id"]) == 12
    assert "srch-secret-name" not in response.text
    assert body["request_id"] in caplog.text  # the detail is in the log, findable by ID


@pytest.mark.parametrize("error", [SettingsError("AZURE_SEARCH_ENDPOINT missing"), RagError("x")])
def test_an_unconfigured_service_is_a_503_without_the_reason(monkeypatch, error):
    def build():
        raise error

    monkeypatch.setattr(api, "_LIVE", api.LazyAnswerer(build))
    response = TestClient(create_app()).post("/ask", json={"question": "q"})
    assert response.status_code == 503
    assert "AZURE_SEARCH_ENDPOINT" not in response.text


def test_the_engine_is_built_once_even_under_concurrent_first_requests():
    builds = []

    def build():
        builds.append(1)
        time.sleep(0.05)  # widen the race window
        return FakeAnswerer()

    lazy = api.LazyAnswerer(build)
    with ThreadPoolExecutor(8) as pool:
        engines = list(pool.map(lambda _: lazy.get(), range(8)))
    assert len(builds) == 1 and len({id(e) for e in engines}) == 1


def test_a_failed_build_is_retried_on_the_next_request():
    attempts = iter([SettingsError("not yet"), None])

    def build():
        error = next(attempts)
        if error:
            raise error
        return FakeAnswerer()

    lazy = api.LazyAnswerer(build)
    with pytest.raises(SettingsError):
        lazy.get()
    assert isinstance(lazy.get(), FakeAnswerer)


def test_graph_mode_and_an_empty_trace_id(make_client):
    answerer = FakeAnswerer(_agent_result(trace_id=""))
    data = make_client(answerer).post("/ask", json={"question": "q", "mode": "graph"}).json()
    assert answerer.calls == [("q", "graph")] and data["trace_id"] is None


def test_an_unreadable_citation_id_is_shown_as_is():
    assert api._citation("not-a-clause").cite == "not-a-clause"


def test_openapi_documents_ask_and_its_errors():
    schema = create_app().openapi()
    ask = schema["paths"]["/ask"]["post"]
    assert {"200", "422", "500", "502", "503"} <= set(ask["responses"])
    fields = schema["components"]["schemas"]["AskResponse"]["properties"]
    assert "shall" in fields["requirement_type"]["enum"]
