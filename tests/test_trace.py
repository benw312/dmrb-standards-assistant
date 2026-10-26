"""JSONL traces: one line per event, run ID and elapsed time on each."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from agent.trace import JsonlTrace, NullTrace


def test_each_event_is_one_json_line(tmp_path):
    ticks = iter([10.0, 10.5, 12.25])
    trace = JsonlTrace(
        tmp_path, clock=lambda: next(ticks), now=lambda: datetime(2026, 10, 25, tzinfo=UTC)
    )
    trace.event("start", question="q")
    trace.event("tool", name="check_limit", arguments={"value": Decimal("4.5")})

    lines = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert trace.path.parent == tmp_path
    assert trace.run_id.startswith("20261025-000000-")
    assert [(e["event"], e["t"]) for e in lines] == [("start", 0.5), ("tool", 2.25)]
    assert lines[1]["arguments"] == {"value": "4.5"}  # Decimal -> string, not a crash
    assert all(e["run_id"] == trace.run_id for e in lines)


def test_two_runs_get_different_files(tmp_path):
    assert JsonlTrace(tmp_path).path != JsonlTrace(tmp_path).path


def test_null_trace_records_nothing():
    assert NullTrace().event("start", question="q") is None


def test_a_failed_write_is_logged_not_raised(tmp_path, caplog):
    trace = JsonlTrace(tmp_path)
    trace.path.mkdir()  # a directory where the file should be: open() fails
    trace.event("start", question="q")
    assert "could not write start event" in caplog.text
