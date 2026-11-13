"""Record the demo's example questions from the live system, for replay without Azure.

    uv run python -m app.record_demo

Asks every example question (app/presentation.EXAMPLES) in every pipeline and writes the
full results to data/demo/answers.json, which agent/replay.py serves when DEMO_REPLAY=1.
Re-run it after anything that changes answers: a re-index, a prompt or tool change, a new
example. It's all or nothing: if any question fails, the old recording is left as it was.

The file quotes short, cited extracts of the DMRB (Crown copyright, OGL v3.0).
"""

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from agent.engine import Answerer, Mode
from agent.loop import AgentResult
from agent.replay import RECORDED_PATH, RecordedSet, Recording
from app.presentation import EXAMPLES

MODES: tuple[Mode, ...] = ("agent", "graph", "rag")


def record(
    answer: Answerer, model: str, today: date, questions: Sequence[str] = EXAMPLES
) -> RecordedSet:
    recordings = []
    for question in questions:
        for mode in MODES:
            result = answer(question, mode)
            if isinstance(result, AgentResult):
                # The trace file stays on this machine, so the recording doesn't point at it.
                result = result.model_copy(update={"trace_id": ""})
            recordings.append(Recording(question=question, mode=mode, result=result))
            print(f"  {mode:<5} {question}")
    return RecordedSet(recorded_on=today, model=model, recordings=tuple(recordings))


def write(recorded: RecordedSet, path: Path = RECORDED_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(recorded.model_dump_json(indent=1) + "\n", encoding="utf-8")


def main() -> int:  # pragma: no cover - wires live Azure clients
    from azure.core.exceptions import AzureError
    from openai import OpenAIError

    from agent.engine import build_live_answerer
    from agent.rag import RagError
    from core.settings import SettingsError, load_settings

    try:
        model = load_settings().chat_deployment
        recorded = record(build_live_answerer(), model, date.today())
    except (SettingsError, RagError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except (AzureError, OpenAIError) as err:
        print(f"error: Azure call failed ({type(err).__name__}); nothing written", file=sys.stderr)
        return 1
    write(recorded)
    print(f"wrote {RECORDED_PATH.relative_to(Path.cwd())}: {len(recorded.recordings)} answers")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
