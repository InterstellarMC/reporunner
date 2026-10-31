"""Unit tests for budget and step ceiling limits."""

from backend.sandbox.api.schemas import RunLimits
from backend.sandbox.exec.limits import BudgetTracker


def test_step_ceiling():
    limits = RunLimits(max_steps=5, max_duration_seconds=100)
    tracker = BudgetTracker("run_test", limits=limits)

    for _ in range(4):
        tracker.record_step()
        assert tracker.check_budget() is None

    tracker.record_step()  # 5th step
    assert tracker.is_step_budget_exhausted()
    assert "Step ceiling reached" in (tracker.check_budget() or "")
