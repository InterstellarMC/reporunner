"""Unit tests for ordered event emitter and append-only event store."""

import os
import shutil
from backend.sandbox.events.emitter import EventEmitter
from backend.sandbox.events.store import EventStore


def test_monotonic_sequence_and_jsonl_store(tmp_path):
    run_dir = str(tmp_path / "test_run_events")
    store = EventStore("run_test_seq", base_dir=run_dir)
    emitter = EventEmitter("run_test_seq", store=store)

    events = []
    emitter.add_sink(events.append)

    e1 = emitter.emit("run.provisioned", {"image": "python:3.11-slim"})
    e2 = emitter.emit("command.started", {"cmd": "pytest"})
    e3 = emitter.emit("command.finished", {"exit_code": 0})

    # Verify seq monotonicity and gapless order
    assert e1["seq"] == 1
    assert e2["seq"] == 2
    assert e3["seq"] == 3

    assert len(events) == 3

    # Verify store replay
    saved_events = store.read_all()
    assert len(saved_events) == 3
    assert [e["seq"] for e in saved_events] == [1, 2, 3]
    assert saved_events[1]["type"] == "command.started"
