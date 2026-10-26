"""The live answer engine: Azure clients built once, answering in any of the three modes.

Shared by the REST API (app/api.py) and the golden-set runner (evals/golden.py), so both
exercise exactly the same wiring.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agent.loop import AgentResult
from agent.rag import RagResult

if TYPE_CHECKING:
    from agent.tools.registry import Tool

Mode = Literal["agent", "graph", "rag"]
Answerer = Callable[[str, Mode], AgentResult | RagResult]


@dataclass(frozen=True)
class LiveEngine:
    answer: Answerer
    tools: "tuple[Tool, ...]"  # the agent's own tools, bound to the same clients


def build_live_answerer() -> Answerer:  # pragma: no cover - wires live Azure clients
    return build_live_engine().answer


def build_live_engine() -> LiveEngine:  # pragma: no cover - wires live Azure clients
    """Build the Azure clients once: a function answering in any mode, and the tools.

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

    return LiveEngine(answer, tools)
