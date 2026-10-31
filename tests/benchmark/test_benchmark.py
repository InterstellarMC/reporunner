"""Unit tests and invariant assertions for the benchmark framework."""

import pytest
from benchmark.runner import BenchmarkRunner


@pytest.fixture
def benchmark_runner():
    return BenchmarkRunner()


def test_manifest_contains_seven_fixable_three_blocked(benchmark_runner: BenchmarkRunner):
    assert benchmark_runner.validate_manifest() is True
    entries = benchmark_runner.entries
    assert len(entries) == 10

    fixable = [e for e in entries if e["expected_outcome"] == "GREEN"]
    blocked = [e for e in entries if e["expected_outcome"] == "BLOCKED"]

    assert len(fixable) == 7
    assert len(blocked) == 3


def test_infrastructure_mode_all_ten_match(benchmark_runner: BenchmarkRunner):
    results = benchmark_runner.run_all()
    assert len(results) == 10

    # Invariant 1: 10/10 must match expected outcome
    for res in results:
        assert res["match"] is True, f"Mismatch on {res['entry_id']}: expected {res['expected']}, got {res['actual']}"

    # Invariant 2: ZERO blocked repos report GREEN
    for res in results:
        if res["expected"] == "BLOCKED":
            assert res["actual"] == "BLOCKED"
            assert res["reproducibility_green"] is False

    # Invariant 3: Hard limit caps: <= 25 steps, <= 600s
    for res in results:
        assert res["steps_used"] <= 25
        assert res["wall_clock_seconds"] <= 600.0
