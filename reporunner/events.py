"""Structured run events – streamed to CLI, saved to JSONL, replayed by the UI.

Event schema:
  {
    "t": "<ISO-8601 timestamp>",
    "kind": "plan|cmd|error|fix|ask|verify|green|stopped|worker|agg|sandbox|refine",
    "stage": "supervisor|worker|aggregator|sandbox|verifier|refinement",
    "run_id": "<id or null>",
    "text": "<short one-line summary>",
    "detail": "<optional multi-line detail or JSON payload>"
  }

Build events first; they drive both the CLI live-stream and the Streamlit replay.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────

class EventKind(str, Enum):
    PLAN      = "plan"
    CMD       = "cmd"
    ERROR     = "error"
    FIX       = "fix"
    ASK       = "ask"
    VERIFY    = "verify"
    GREEN     = "green"
    STOPPED   = "stopped"
    WORKER    = "worker"
    AGG       = "agg"
    SANDBOX   = "sandbox"
    REFINE    = "refine"
    STATUS    = "status"


class EventStage(str, Enum):
    SUPERVISOR  = "supervisor"
    WORKER      = "worker"
    AGGREGATOR  = "aggregator"
    SANDBOX     = "sandbox"
    VERIFIER    = "verifier"
    REFINEMENT  = "refinement"
    SYSTEM      = "system"


class WorkerStatus(str, Enum):
    QUEUED    = "QUEUED"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    BLOCKED   = "BLOCKED"


class VerifyStatus(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED   = "RED"


# ──────────────────────────────────────────────────────────────
# Event dataclass
# ──────────────────────────────────────────────────────────────

@dataclass
class RunEvent:
    kind: EventKind
    text: str
    stage: EventStage = EventStage.SYSTEM
    run_id: Optional[str] = None
    detail: Optional[Any] = None
    t: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["stage"] = self.stage.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "RunEvent":
        return cls(
            kind=EventKind(d["kind"]),
            text=d.get("text", ""),
            stage=EventStage(d.get("stage", "system")),
            run_id=d.get("run_id"),
            detail=d.get("detail"),
            t=d.get("t", datetime.now(timezone.utc).isoformat()),
        )

    @classmethod
    def from_json(cls, s: str) -> "RunEvent":
        return cls.from_dict(json.loads(s))


# ──────────────────────────────────────────────────────────────
# JSONL run log
# ──────────────────────────────────────────────────────────────

def append_event(run_dir: Path, event: RunEvent) -> None:
    """Append a single event to runs/<id>/events.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(event.to_json() + "\n")


def load_events(run_dir: Path) -> list[RunEvent]:
    """Load all events from runs/<id>/events.jsonl."""
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    events: list[RunEvent] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    events.append(RunEvent.from_json(line))
                except Exception:
                    pass
    return events
