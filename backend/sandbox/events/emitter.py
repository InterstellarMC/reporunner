"""Ordered event emitter with monotonic sequence numbers and pluggable sinks."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from backend.sandbox.events.store import EventStore

logger = logging.getLogger(__name__)


class EventEmitter:
    """Emits monotonic, structured events to local store and listeners."""

    def __init__(self, run_id: str, store: Optional[EventStore] = None):
        self.run_id = run_id
        self.store = store or EventStore(run_id)
        self.seq = 0
        self._lock = threading.Lock()
        self.sinks: List[Callable[[Dict[str, Any]], None]] = [self.store.append]

    def add_sink(self, sink: Callable[[Dict[str, Any]], None]) -> None:
        """Register an extra sink (e.g. WebSocket, stdout, or UI callback)."""
        self.sinks.append(sink)

    def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        caused_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Emit a structured event with guaranteed monotonic sequence number.
        Returns the emitted event dictionary.
        """
        with self._lock:
            self.seq += 1
            event = {
                "event_id": str(uuid.uuid4()),
                "run_id": self.run_id,
                "seq": self.seq,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "execution",
                "type": event_type,
                "payload": payload,
            }
            if caused_by:
                event["caused_by"] = caused_by

            for sink in self.sinks:
                try:
                    sink(event)
                except Exception as e:
                    logger.warning("Event sink error for event %s: %s", event_type, e)

            return event
