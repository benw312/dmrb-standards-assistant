"""Recorded answers, replayed: the demo and API without an Azure subscription.

    DEMO_REPLAY=1 uv run streamlit run app/demo.py

The project isn't hosted, so someone who clones it has no index and no model to ask. This
answerer returns results recorded from the live system (app/record_demo.py writes them to
data/demo/answers.json) for the demo's example questions, in each pipeline. They are real
runs, stored whole: answer, citations, checks, tool calls, tokens and timing, so everything
downstream (response shape, banners, review queue) behaves as it does live.

It never improvises: a question that wasn't recorded raises NotRecordedError rather than
returning the nearest match, because a replayed answer to a different question would be a
wrong answer with real citations on it.
"""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.loop import AgentResult
from agent.rag import RagResult

RECORDED_PATH = Path(__file__).resolve().parent.parent / "data" / "demo" / "answers.json"
REPLAY_ENV = "DEMO_REPLAY"  # "1" switches the engine to recorded answers

Mode = Literal["agent", "graph", "rag"]


class ReplayError(RuntimeError):
    """The recording is missing or unreadable."""


class NotRecordedError(LookupError):
    """Replay mode was asked a question it has no recording for."""


class Recording(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    mode: Mode
    result: AgentResult | RagResult


class RecordedSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recorded_on: date
    model: str  # the chat deployment that answered
    recordings: tuple[Recording, ...]


def _key(question: str) -> str:
    """Case and spacing don't make it a different question; anything else does."""
    return " ".join(question.split()).casefold()


def load_recorded(path: Path | None = None) -> RecordedSet:
    path = path or RECORDED_PATH
    try:
        return RecordedSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise ReplayError(
            f"no recorded answers at {path.name}: run `uv run python -m app.record_demo`"
        ) from None
    except (OSError, ValueError, ValidationError) as err:
        raise ReplayError(f"{path.name} isn't a valid recording: {err}") from err


class ReplayAnswerer:
    """Callable like the live answerer: (question, mode) -> the recorded result."""

    def __init__(self, recorded: RecordedSet) -> None:
        self.recorded_on, self.model = recorded.recorded_on, recorded.model
        self._results: dict[tuple[str, str], AgentResult | RagResult] = {}
        for r in recorded.recordings:
            key = (_key(r.question), r.mode)
            if key in self._results:
                raise ReplayError(f"recorded twice: {r.question!r} ({r.mode})")
            self._results[key] = r.result
        self.questions = tuple(dict.fromkeys(r.question for r in recorded.recordings))

    def __call__(self, question: str, mode: Mode) -> AgentResult | RagResult:
        try:
            return self._results[(_key(question), mode)]
        except KeyError:
            raise NotRecordedError(
                "This is the recorded demo: it only has answers for the example questions."
            ) from None
