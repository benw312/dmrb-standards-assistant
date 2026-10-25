"""JSONL traces: one file per agent run, one line per event, for reading what the agent did.

    traces/20261025-213012-3f9a1c.jsonl   (git-ignored)

Events: start, model (each model call), tool (each tool call), check (each citation check)
and end. Every line carries the run ID and seconds since the start, so a trace sorts and
filters with jq or pandas without parsing anything else.
"""

import json
import logging
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

TRACE_DIR = Path(__file__).resolve().parent.parent / "traces"


class Trace(Protocol):
    run_id: str

    def event(self, kind: str, **data: Any) -> None: ...


class NullTrace:
    """Records nothing: for tests and callers that don't want files."""

    run_id = "untraced"

    def event(self, kind: str, **data: Any) -> None:
        return None


class JsonlTrace:
    """Appends each event to traces/<run_id>.jsonl as it happens, so a crash keeps the
    events before it."""

    def __init__(
        self,
        directory: Path = TRACE_DIR,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.run_id = f"{now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        self._clock = clock
        self._started = clock()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.run_id}.jsonl"

    def event(self, kind: str, **data: Any) -> None:
        line = {
            "run_id": self.run_id,
            "t": round(self._clock() - self._started, 3),
            "event": kind,
            **data,
        }
        try:
            with self.path.open("a", encoding="utf-8") as f:
                # default=str: Decimals and other values from tool arguments become strings.
                f.write(json.dumps(line, default=str, ensure_ascii=False) + "\n")
        except OSError as err:
            # A full disk shouldn't cost the engineer their answer; the gap is logged.
            logger.warning("Trace %s: could not write %s event: %s", self.run_id, kind, err)
