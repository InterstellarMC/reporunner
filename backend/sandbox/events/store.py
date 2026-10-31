"""Append-only JSONL event store and replay engine."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List


class EventStore:
    """Manages append-only JSONL log file for a run."""

    def __init__(self, run_id: str, base_dir: str = "runs"):
        self.run_id = run_id
        self.run_dir = os.path.join(base_dir, run_id)
        self.log_path = os.path.join(self.run_dir, "events.jsonl")
        os.makedirs(self.run_dir, exist_ok=True)

    def append(self, event: Dict[str, Any]) -> None:
        """Atomically append structured event to JSONL file."""
        line = json.dumps(event) + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all events recorded for this run in sequence."""
        if not os.path.exists(self.log_path):
            return []
        events = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """Stream events line by line."""
        if not os.path.exists(self.log_path):
            return
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
