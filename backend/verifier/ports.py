"""Protocols for verification engine decoupling."""

from __future__ import annotations

from typing import Any, Dict, Protocol, Tuple


class CommandRunner(Protocol):
    """Protocol for executing commands inside the environment without sandbox coupling."""

    def run(self, cmd: str, timeout_s: int = 120, cwd: str = "/workspace") -> Tuple[int, str, str]:
        """
        Execute command.
        Returns (exit_code, stdout, stderr).
        """
        ...


class EventSink(Protocol):
    """Protocol for emitting verification events."""

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        ...
