"""REST API over the assistant: POST a question, get a cited, checked answer as JSON.

    uv run uvicorn app.api:app --reload        # then open http://127.0.0.1:8000/docs

One endpoint does the work: POST /ask with {"question": ..., "mode": "agent" | "graph" | "rag"}.
The response carries the answer (disclaimer included), its citations in display form,
requirement strength, the departure and review flags, why it escalated if it did, and cost.
A flagged answer is still a 200: needing an engineer is a result, not an error.

Errors: 422 bad request; 503 the service isn't configured (settings or system prompt
missing); 502 an Azure call failed; 500 anything else. Error bodies carry only a request ID,
which is in the server log next to the detail.

The answer engine is a FastAPI dependency, built once from the live Azure clients on the
first request. Tests replace it with a fake, so the API is tested without Azure.

Not here, and handled in front of the service in production (docs/ARCHITECTURE.md):
authentication, rate limiting and quotas (Azure API Management).
"""

import logging
import threading
import uuid
from collections.abc import Callable
from typing import Annotated, Literal

from azure.core.exceptions import AzureError
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from openai import OpenAIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.answer import Confidence, RequirementType
from agent.citations import CitationError, display_citation
from agent.loop import AgentResult
from agent.rag import RagError, RagResult, with_disclaimer
from core.settings import SettingsError

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 1000
RAG_RETRIEVAL = "semantic search, top 8 (fixed, not chosen by the model)"
Mode = Literal["agent", "graph", "rag"]
Answerer = Callable[[str, Mode], AgentResult | RagResult]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    mode: Mode = Field(
        default="agent",
        description="agent: tool-calling loop; graph: the same agent on LangGraph; "
        "rag: single retrieval and one model call",
    )

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class Citation(BaseModel):
    id: str = Field(description="Chunk ID, e.g. 'CD109-5.1'")
    cite: str = Field(description="As written in the answer, e.g. '[CD 109 cl. 5.1]'")


class AskResponse(BaseModel):
    request_id: str
    question: str
    mode: Mode
    answer: str
    citations: list[Citation]
    requirement_type: RequirementType
    is_departure: bool | None
    confidence: Confidence
    needs_human_review: bool
    escalation: list[str]
    tools_used: list[str]
    input_tokens: int
    output_tokens: int
    seconds: float
    trace_id: str | None


class ErrorBody(BaseModel):
    detail: str
    request_id: str


def _citation(chunk_id: str) -> Citation:
    try:
        return Citation(id=chunk_id, cite=display_citation(chunk_id))
    except CitationError:
        return Citation(id=chunk_id, cite=chunk_id)


def to_response(result: AgentResult | RagResult, mode: Mode, request_id: str) -> AskResponse:
    a = result.answer
    agent = isinstance(result, AgentResult)
    return AskResponse(
        request_id=request_id,
        question=result.question,
        mode=mode,
        answer=with_disclaimer(a.answer),
        citations=[_citation(c) for c in a.citations],
        requirement_type=a.requirement_type,
        is_departure=a.is_departure,
        confidence=a.confidence,
        needs_human_review=result.needs_human_review,
        escalation=list(result.escalation),
        tools_used=[c["name"] for c in result.tool_calls] if agent else [RAG_RETRIEVAL],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        seconds=result.seconds,
        trace_id=(result.trace_id or None) if agent else None,
    )


def build_live_answerer() -> Answerer:  # pragma: no cover - wires live Azure clients
    """Build the Azure clients and return a function answering in any mode.

    The tools are closures over these clients and hold no per-request state, so one set is
    shared by all worker threads; the OpenAI, httpx and Search clients are thread-safe.
    """
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    from agent.graph import run_graph
    from agent.loop import run_agent
    from agent.rag import answer_question, load_system_prompt, make_retriever
    from agent.tools.registry import build_tools
    from agent.trace import JsonlTrace
    from core.clients import chat_client, embedding_client
    from core.settings import load_settings
    from index.embed import embed_texts

    settings = load_settings()
    system_prompt = load_system_prompt()
    credential = DefaultAzureCredential()
    embeddings = embedding_client(settings, credential).embeddings

    def embed(text: str) -> list[float]:
        return embed_texts(embeddings, settings.embedding_deployment, [text])[0]

    search_client = SearchClient(
        settings.search_endpoint, settings.search_index, credential=credential
    )
    responses = chat_client(settings, credential).responses
    tools = build_tools(search_client, embed)
    retrieve = make_retriever(search_client, embed)
    model = settings.chat_deployment

    def answer(question: str, mode: Mode) -> AgentResult | RagResult:
        if mode == "rag":
            return answer_question(question, retrieve, responses, model, system_prompt)
        runner = run_graph if mode == "graph" else run_agent
        # Traces go to local disk (traces/); in production they'd go to App Insights.
        return runner(question, tools, responses, model, system_prompt, trace=JsonlTrace())

    return answer


class LazyAnswerer:
    """Builds the answerer on first use, once, even when first requests arrive together
    (lru_cache would let each concurrent miss build its own clients). A failed build isn't
    kept, so fixing the config takes effect on the next request without a restart."""

    def __init__(self, build: Callable[[], Answerer]) -> None:
        self._build, self._answerer, self._lock = build, None, threading.Lock()

    def get(self) -> Answerer:
        if self._answerer is None:
            with self._lock:
                if self._answerer is None:  # another thread may have built it meanwhile
                    self._answerer = self._build()
        return self._answerer


_LIVE = LazyAnswerer(build_live_answerer)


def get_answerer() -> Answerer:
    """The engine, or a 503 if the service can't be configured (logged, not echoed: the
    messages name environment variables)."""
    try:
        return _LIVE.get()
    except (SettingsError, RagError, AzureError, ValueError) as err:
        logger.error("Answer engine not configured: %s", err)
        raise HTTPException(status_code=503, detail="The service is not configured.") from err


def _fail(status: int, detail: str, request_id: str) -> JSONResponse:
    return JSONResponse(ErrorBody(detail=detail, request_id=request_id).model_dump(), status)


def create_app() -> FastAPI:
    app = FastAPI(
        title="DMRB Standards Assistant",
        version="0.1.0",
        description="Answers UK highway design questions from the DMRB with checked clause "
        "citations. Decision support only; confirm against the current standard.",
    )

    @app.get("/health", tags=["ops"], summary="Liveness")
    async def health() -> dict[str, str]:
        # async and I/O-free, so it runs on the event loop and still answers when every
        # worker thread is busy with a slow /ask.
        return {"status": "ok"}

    @app.post(
        "/ask",
        response_model=AskResponse,
        tags=["answers"],
        summary="Answer a DMRB question with checked citations",
        responses={
            500: {"model": ErrorBody},
            502: {"model": ErrorBody, "description": "An Azure service call failed"},
            503: {"description": "The service is not configured"},
        },
    )
    def ask(
        request: AskRequest, answerer: Annotated[Answerer, Depends(get_answerer)]
    ) -> AskResponse | JSONResponse:
        # A plain `def`, not `async def`: the Azure SDK clients are synchronous, and FastAPI
        # runs sync endpoints in a worker thread so they don't stall the event loop.
        request_id = uuid.uuid4().hex[:12]
        try:
            return to_response(answerer(request.question, request.mode), request.mode, request_id)
        except (AzureError, OpenAIError):
            logger.exception("Request %s: Azure call failed", request_id)
            return _fail(502, "An Azure service call failed.", request_id)
        except Exception:  # noqa: BLE001 - a stable body instead of a bare 500
            logger.exception("Request %s: unexpected error", request_id)
            return _fail(500, "Internal error.", request_id)

    return app


app = create_app()
