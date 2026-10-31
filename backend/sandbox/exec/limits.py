"""Resource, step, and timeout limits controller."""

from __future__ import annotations

import time
from typing import Optional
from backend.sandbox.api.schemas import RunLimits


class BudgetTracker:
    """Tracks step count and wall-clock time against run limits."""

    def __init__(self, run_id: str, limits: Optional[RunLimits] = None):
        self.run_id = run_id
        self.limits = limits or RunLimits()
        self.step_count = 0
        self.start_time = time.monotonic()
        self._is_closed = False

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def remaining_seconds(self) -> float:
        rem = self.limits.max_duration_seconds - self.elapsed_seconds
        return max(0.0, rem)

    def record_step(self) -> None:
        """Increment step counter and check budget."""
        self.step_count += 1

    def is_step_budget_exhausted(self) -> bool:
        return self.step_count >= self.limits.max_steps

    def is_time_budget_exhausted(self) -> bool:
        return self.elapsed_seconds >= self.limits.max_duration_seconds

    def check_budget(self) -> Optional[str]:
        """Returns error message if budget is exhausted, else None."""
        if self.is_step_budget_exhausted():
            return f"Step ceiling reached ({self.step_count}/{self.limits.max_steps} steps)"
        if self.is_time_budget_exhausted():
            return f"Wall-clock deadline reached ({self.elapsed_seconds:.1f}s >= {self.limits.max_duration_seconds}s)"
        return None

    def close(self) -> None:
        self._is_closed = True
