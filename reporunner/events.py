"""Structured events streamed to UI/CLI."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from backend.sandbox.events.emitter import EventEmitter
from backend.sandbox.events.store import EventStore

__all__ = ["EventEmitter", "EventStore"]
