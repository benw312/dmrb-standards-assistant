"""Phase 3 RAG baseline: question -> hybrid search (top 8) -> prompt -> structured Answer.

    uv run python -m agent.rag "What is the maximum gradient on a motorway?"

The model must answer only from the retrieved clauses and cite each claim inline. After it
answers, the citations are checked in code (agent/citations.py): any clause it cites that it
was never shown fails the check, and the model gets one retry with the reasons. A second
failure is escalated: the result carries the reasons, `needs_human_review` is forced on, and
the CLI prints the text under an UNVERIFIED banner, never as a clean answer.

The system prompt has two parts. Ben writes the domain rules in agent/prompts/system.md; the
output contract below (citation format, field meanings) lives in code, because the citation
checker depends on it and the two must never drift apart.
"""

import argparse
import html
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.answer import Answer
from agent.citations import CitationCheck, CitationError, display_citation, verify_citations
from index.search import Hit, Mode

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.md"
TOP_K = 8  # build guide: hybrid search, top 8
MAX_ATTEMPTS = 2  # the first answer, plus one retry after a failed citation check
CONTEXT_FIELDS = ("doc_code", "version", "kind", "requirement_type", "heading_path", "page", "text")

OUTPUT_CONTRACT = """\
## Output format (checked in code)

- Cite every claim inline using the exact `cite` form given with each clause, e.g.
  [CD 109 cl. 5.3.1] or [CD 109 Table 2.10]. Several in one bracket: [CD 109 cl. 5.3.1, 5.3.2],
  [CD 109 cl. 5.1; CD 109 Table 5.1] or [CD 109 Table 5.1; CD 109 Table 5.2].
- Every mention of a clause, table or figure must be one of those bracketed citations: write
  "the permitted relaxations [CD 109 Table 5.1]", not "the values in Table 5.1". When quoting
  the standard's own wording, put the quote in double quotes; references inside a quote are
  fine as printed.
- Only cite clauses given below. Citing anything else fails the check.
- `citations` lists the IDs you relied on exactly as given in `id`, e.g. "CD109-5.3.1", and
  must match the inline citations.
- `requirement_type` is the strength of the governing clause: must, shall, should, may or
  note. Use "not_covered" when the clauses don't answer the question. A not_covered answer
  cites nothing and names no clause or table, not even to say why it doesn't apply: say the
  indexed standards don't cover it and, if you can, which standard is likely to.
- Don't calculate anything: no arithmetic, interpolation or unit conversion. Quote values as
  the clauses and tables give them. If the answer needs a calculation (an equation, a value
  between two table entries), name the equation or table, say a calculation is needed, and
  set `needs_human_review`.
- `is_departure` is true or false only when the question describes a design that might need
  a Departure from Standard; otherwise null.
- Set `needs_human_review` when you are unsure, the clauses conflict, or a departure is
  possible.
"""


class RagError(RuntimeError):
    """The pipeline can't run (e.g. the system prompt hasn't been written yet)."""


class ParseAPI(Protocol):
    """`OpenAI().responses`: just the call this module makes."""

    def parse(self, *, model: str, instructions: str, input: list, text_format: type) -> Any: ...  # noqa: A002


Retriever = Callable[[str], list[Hit]]


class RagResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    answer: Answer
    retrieved: tuple[str, ...]  # chunk IDs shown to the model, best first
    check: CitationCheck  # of the final attempt
    attempts: int
    escalation: tuple[str, ...] = ()  # why an engineer should look, empty if none
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    @property
    def needs_human_review(self) -> bool:
        return self.answer.needs_human_review or bool(self.escalation)


def load_system_prompt(path: Path = PROMPT_PATH) -> str:
    """Ben's rules plus the code-owned output contract."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise RagError(
            f"{path.name} is missing or empty: Ben writes the system prompt there "
            "(rules in the build guide p.5; see docs/HANDOVER.md)"
        )
    return f"{path.read_text(encoding='utf-8').strip()}\n\n{OUTPUT_CONTRACT}"


def format_context(hits: Sequence[Hit]) -> str:
    """The retrieved chunks as the model sees them, each with the citation form to copy."""
    blocks = []
    for hit in hits:
        f = hit.fields
        strength = f.get("requirement_type") or "-"
        attrs = {
            "id": hit.citation_id,
            "cite": _cite_form(hit.citation_id),
            "standard": f"{f.get('doc_code') or ''} {f.get('version') or ''}".strip(),
            "kind": f.get("kind") or "",
            "strength": strength,
            "page": f.get("page") or "",
        }
        # Escaped so text from any source can't break out of its block or attributes.
        head = " ".join(f'{k}="{html.escape(str(v), quote=True)}"' for k, v in attrs.items())
        body = f"{f.get('heading_path') or ''}\n\n{f.get('text') or ''}"
        blocks.append(f"<clause {head}>\n{body.replace('</clause>', '</ clause>')}\n</clause>")
    return "\n\n".join(blocks)


def _cite_form(citation_id: str) -> str:
    """The inline citation to copy; a chunk type the grammar can't read yet falls back to its
    ID, so one odd chunk can't fail the whole question."""
    try:
        return display_citation(citation_id)
    except CitationError:
        return citation_id


def _first_message(question: str, hits: Sequence[Hit]) -> dict:
    return {
        "role": "user",
        "content": f"Clauses retrieved for this question:\n\n{format_context(hits)}\n\n"
        f"Question: {question}",
    }


# Shown when no usable answer came back. It cites nothing, so it can't fail the check.
FALLBACK_ANSWER = Answer(
    answer="No answer could be produced; an engineer needs to look at this question.",
    citations=(),
    requirement_type="not_covered",
    is_departure=None,
    confidence="low",
    needs_human_review=True,
)

# A fixed sentence is added in code: asked for in the prompt, the model dropped it 3 times in 10.
DISCLAIMER = "Decision support only; confirm against the current standard."


def with_disclaimer(text: str) -> str:
    """The answer text with the disclaimer as its last line, never twice."""
    body = text.rstrip()
    if body.endswith(DISCLAIMER):
        body = body[: -len(DISCLAIMER)].rstrip()
    return f"{body}\n\n{DISCLAIMER}"


RETRY_INSTRUCTION = "Answer again, citing only the clauses given, in the format required."
NO_ANSWER = "The model returned no answer (declined, cut short or filtered)."


def retry_message(check: CitationCheck) -> dict:
    reasons = "\n".join(f"- {r}" for r in check.reasons)
    return {
        "role": "user",
        "content": f"Your answer failed the citation check:\n{reasons}\n{RETRY_INSTRUCTION}",
    }


@dataclass(frozen=True)
class _Reply:
    answer: Answer | None
    problem: str | None = None  # why there's no usable answer
    retryable: bool = True  # False when asking again won't help (the model declined)


def _ask(
    responses: ParseAPI, model: str, system_prompt: str, messages: list[dict], usage: "Usage"
) -> _Reply:
    """One model call."""
    try:
        response = responses.parse(
            model=model, instructions=system_prompt, input=messages, text_format=Answer
        )
    except ValidationError as err:
        # The SDK validates the output against Answer itself, so output that fits the JSON
        # schema but breaks a rule (a blank answer, seen live) raises here. Its token usage
        # is lost with the response; it's a rare path.
        first = err.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "output"
        return _Reply(None, f"Your output was not a valid answer ({field}: {first['msg']}).")
    usage.add(response)
    if response.output_parsed is None:
        # A refusal, or output cut short or filtered: nothing to correct, so no retry.
        return _Reply(None, NO_ANSWER, retryable=False)
    return _Reply(response.output_parsed)


@dataclass
class Usage:
    """Token totals across a question's model calls (shared with the agent loop)."""

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0


def answer_question(
    question: str,
    retrieve: Retriever,
    responses: ParseAPI,
    model: str,
    system_prompt: str,
    clock: Callable[[], float] = time.monotonic,
) -> RagResult:
    """Retrieve, answer, check citations; retry once; escalate if it still fails."""
    if not question.strip():
        raise ValueError("empty question")
    started = clock()
    hits = retrieve(question)
    retrieved = tuple(h.chunk_id for h in hits)
    messages: list[dict] = [_first_message(question, hits)]
    usage = Usage()
    answer, check, reasons, attempts = None, None, [], 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        reply = _ask(responses, model, system_prompt, messages, usage)
        if reply.answer is None:
            reasons.append(f"Attempt {attempt}: {reply.problem}")
            if not reply.retryable:
                break
            messages.append({"role": "user", "content": f"{reply.problem}\n{RETRY_INSTRUCTION}"})
            continue
        # Keep the latest answer that parsed, so a later unusable reply can't erase it.
        answer, check = reply.answer, verify_citations(reply.answer, retrieved)
        if check.ok:
            reasons = []
            break
        reasons.append(f"Attempt {attempt}: citation check failed: {' '.join(check.reasons)}")
        messages += [
            {"role": "assistant", "content": answer.model_dump_json()},
            retry_message(check),
        ]
    escalation = tuple(reasons)
    return _result(
        question, answer, check, retrieved, attempts, escalation, usage, clock() - started
    )


def _result(
    question: str,
    answer: Answer | None,
    check: CitationCheck | None,
    retrieved: tuple[str, ...],
    attempts: int,
    escalation: tuple[str, ...],
    usage: Usage,
    seconds: float,
) -> RagResult:
    if answer is None:
        answer = FALLBACK_ANSWER
        check = verify_citations(answer, retrieved)
    if escalation:
        # Never show a failed answer as if it passed: the flag travels with it.
        answer = answer.model_copy(update={"needs_human_review": True})
    return RagResult(
        question=question,
        answer=answer,
        retrieved=retrieved,
        check=check,
        attempts=attempts,
        escalation=escalation,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        seconds=round(seconds, 2),
    )


def make_retriever(search_client: Any, embed: Callable[[str], list[float]]) -> Retriever:
    """Semantic hybrid search for the top 8 chunks, with the fields the prompt needs."""
    from index.search import search

    def retrieve(question: str) -> list[Hit]:
        return search(
            search_client, question, embed(question), Mode.SEMANTIC, TOP_K, select=CONTEXT_FIELDS
        )

    return retrieve


def render(result: RagResult) -> str:
    a = result.answer
    banner = ["UNVERIFIED: failed the checks below; an engineer must review it.", ""]
    lines = [
        *(banner if result.escalation else []),
        with_disclaimer(a.answer),
        "",
        f"requirement: {a.requirement_type} | confidence: {a.confidence} | "
        f"departure: {a.is_departure} | human review: {result.needs_human_review}",
        f"cited: {', '.join(a.citations) or 'none'}",
        f"retrieved: {', '.join(result.retrieved)}",
    ]
    lines += [f"escalation: {reason}" for reason in result.escalation]
    lines.append(
        f"{result.attempts} attempt(s), {result.input_tokens} in / {result.output_tokens} out "
        f"tokens, {result.seconds}s"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wires live Azure clients
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from openai import OpenAIError

    from core.clients import chat_client, embedding_client
    from core.settings import SettingsError, load_settings
    from index.embed import embed_texts

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        system_prompt = load_system_prompt()
    except (SettingsError, RagError) as err:
        print(f"Error: {err}")
        return 1
    credential = DefaultAzureCredential()
    embeddings = embedding_client(settings, credential).embeddings
    search_client = SearchClient(
        settings.search_endpoint, settings.search_index, credential=credential
    )
    retrieve = make_retriever(
        search_client, lambda q: embed_texts(embeddings, settings.embedding_deployment, [q])[0]
    )
    try:
        result = answer_question(
            args.question,
            retrieve,
            chat_client(settings, credential).responses,
            settings.chat_deployment,
            system_prompt,
        )
    except (AzureError, OpenAIError) as err:
        print(f"Error: Azure call failed ({type(err).__name__}): {str(err).splitlines()[0]}")
        return 1
    print(json.dumps(result.model_dump(mode="json"), indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
