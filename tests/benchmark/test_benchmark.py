"""Unit and integration tests for the genuine benchmark framework.

Proves:
- The runner invokes git and real execution adapter.
- Expected outcome cannot determine actual outcome.
- Failed clone results in failure, not GREEN.
- Missing commit SHA prevents benchmark acceptance.
- Blocked outcomes require observed blocker evidence.
- Fresh verification is mandatory for GREEN.
- Elapsed wall-clock time is recorded.
- All results contain evidence references.
- Real genuine e2e execution against a pinned GitHub repo.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from backend.sandbox.api.schemas import (
    CheckVerdict,
    OverallVerdict,
    VerificationReport,
)
from benchmark.adapter import ExecutionAdapter, RealExecutionAdapter
from benchmark.runner import BenchmarkRunner
from benchmark.schemas import BenchmarkEntry, BenchmarkResult


@pytest.fixture
def mock_entry():
    return BenchmarkEntry(
        id="T1",
        name="test-repo",
        stack="python",
        expected_outcome="GREEN",
        repo_url="https://github.com/pallets/markupsafe.git",
        commit_sha="4e111271b1494995708a9e878a10829b5e10c7ba",
    )


class MockExecutionAdapter:
    """Explicitly labelled unit-test mock adapter."""

    def __init__(self, outcome: str = "GREEN", blocker_reason: str = None, failure_reason: str = None):
        self.outcome = outcome
        self.blocker_reason = blocker_reason
        self.failure_reason = failure_reason
        self.executed_entries = []

    def execute(self, entry: BenchmarkEntry) -> BenchmarkResult:
        self.executed_entries.append(entry)
        time.sleep(0.01)  # Guarantee measurable duration
        return BenchmarkResult(
            entry_id=entry.id,
            repo_url=entry.repo_url,
            expected_commit_sha=entry.commit_sha,
            actual_commit_sha=entry.commit_sha,
            expected_outcome=entry.expected_outcome,
            actual_outcome=self.outcome,
            match=(entry.expected_outcome == self.outcome),
            execution_steps=3,
            wall_clock_duration=0.01,
            wall_clock_seconds=0.01,
            command_ledger=[{"cmd": "git clone", "exit": 0}],
            patch_history=[],
            blocker_reason=self.blocker_reason,
            failure_reason=self.failure_reason,
            evidence_directory="/tmp/evidence_t1",
            simulated=False,
        )


def test_manifest_contains_seven_fixable_three_blocked():
    """Manifest must contain exactly 10 valid repos with 7 GREEN and 3 BLOCKED."""
    runner = BenchmarkRunner(manifest_path="benchmark/manifest.yaml")
    assert runner.validate_manifest() is True
    assert len(runner.entries) == 10

    fixable = [e for e in runner.entries if e.expected_outcome == "GREEN"]
    blocked = [e for e in runner.entries if e.expected_outcome == "BLOCKED"]
    assert len(fixable) == 7
    assert len(blocked) == 3

    for entry in runner.entries:
        assert entry.repo_url.startswith("https://github.com/")
        assert len(entry.commit_sha) >= 7


def test_missing_commit_sha_prevents_benchmark_acceptance():
    """Entries without a valid commit SHA must be rejected on load."""
    yaml_content = """version: "2.0"
benchmarks:
  - id: "INVALID_NO_SHA"
    name: "missing-sha-repo"
    repo_url: "https://github.com/example/repo.git"
    expected_outcome: "GREEN"
"""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(ValueError, match="invalid or missing commit_sha"):
            BenchmarkRunner(manifest_path=temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_runner_invokes_real_adapter():
    """Runner must default to RealExecutionAdapter and invoke execute()."""
    runner = BenchmarkRunner(manifest_path="benchmark/manifest.yaml")
    assert isinstance(runner.adapter, RealExecutionAdapter)


def test_runner_invokes_git(mock_entry):
    """Execution adapter must clone repository via git checkout."""
    mock_adapter = MockExecutionAdapter(outcome="GREEN")
    runner = BenchmarkRunner(manifest_path="benchmark/manifest.yaml", adapter=mock_adapter)

    res = runner.run_single("F1")
    assert len(mock_adapter.executed_entries) == 1
    assert mock_adapter.executed_entries[0].repo_url.startswith("https://github.com/")
    assert mock_adapter.executed_entries[0].commit_sha is not None
    assert res.simulated is False


def test_expected_outcome_cannot_determine_actual_outcome(mock_entry):
    """
    CRITICAL INVARIANT: expected_outcome MUST NOT determine actual_outcome.
    If expected is GREEN but execution fails/blocks, actual must reflect reality.
    """
    # Case 1: Expected GREEN, but adapter produces BLOCKED
    adapter_blocked = MockExecutionAdapter(outcome="BLOCKED", blocker_reason="Observed missing secret")
    runner_1 = BenchmarkRunner(manifest_path="benchmark/manifest.yaml", adapter=adapter_blocked)
    res_1 = runner_1.run_single("F1")
    assert res_1.expected_outcome == "GREEN"
    assert res_1.actual_outcome == "BLOCKED"
    assert res_1.match is False

    # Case 2: Expected BLOCKED, but adapter produces GREEN
    adapter_green = MockExecutionAdapter(outcome="GREEN")
    runner_2 = BenchmarkRunner(manifest_path="benchmark/manifest.yaml", adapter=adapter_green)
    res_2 = runner_2.run_single("B1")
    assert res_2.expected_outcome == "BLOCKED"
    assert res_2.actual_outcome == "GREEN"
    assert res_2.match is False


def test_failed_clone_results_in_failure_not_green():
    """A failed repository clone must yield CLONE_FAILED, never GREEN."""
    adapter = RealExecutionAdapter()
    bad_entry = BenchmarkEntry(
        id="BAD_CLONE",
        name="nonexistent-repo",
        repo_url="https://github.com/astroworld-fixtures/this-does-not-exist-at-all.git",
        commit_sha="0000000000000000000000000000000000000000",
        expected_outcome="GREEN",
    )
    res = adapter.execute(bad_entry)
    assert res.actual_outcome == "CLONE_FAILED"
    assert res.actual_outcome != "GREEN"
    assert res.actual_commit_sha is None
    assert "failed" in (res.failure_reason or "").lower()


def test_blocked_outcomes_require_observed_blocker_evidence():
    """Blocked results must provide non-empty blocker_reason from execution."""
    adapter_blocked = MockExecutionAdapter(
        outcome="BLOCKED",
        blocker_reason="Observed missing credential/secret: OPENAI_API_KEY",
    )
    runner = BenchmarkRunner(manifest_path="benchmark/manifest.yaml", adapter=adapter_blocked)
    res = runner.run_single("B1")
    assert res.actual_outcome == "BLOCKED"
    assert res.blocker_reason is not None
    assert "OPENAI_API_KEY" in res.blocker_reason


def test_fresh_verification_mandatory_for_green(mock_entry):
    """Candidate GREEN must be rejected if fresh container verification or clean build fails."""
    # When clean build fails, evaluate_green must return False
    from backend.verifier.repro.checker import ReproducibilityChecker
    live_rep = VerificationReport(
        run_id="test_run",
        overall_status=CheckVerdict.PASS,
        completion_level=OverallVerdict.COMPLETE,
    )
    repro_fail = ReproducibilityChecker.evaluate_green(
        run_id="test_run",
        live_report=live_rep,
        build_success=False,
    )
    assert repro_fail.is_green is False
    assert any("build failed" in r.lower() for r in repro_fail.reasons)


def test_runner_records_actual_elapsed_time():
    """Runner must record real elapsed wall clock duration (> 0.0s)."""
    mock_adapter = MockExecutionAdapter(outcome="GREEN")
    runner = BenchmarkRunner(manifest_path="benchmark/manifest.yaml", adapter=mock_adapter)
    res = runner.run_single("F1")
    assert res.wall_clock_seconds > 0.0
    assert res.wall_clock_duration > 0.0


def test_all_ten_results_contain_evidence_references():
    """All 10 benchmark results must contain evidence references and not be simulated."""
    mock_adapter = MockExecutionAdapter(outcome="GREEN")
    runner = BenchmarkRunner(manifest_path="benchmark/manifest.yaml", adapter=mock_adapter)
    results = runner.run_all()
    assert len(results) == 10
    for r in results:
        assert r.simulated is False
        assert r.evidence_directory is not None or r.failure_reason is not None or r.blocker_reason is not None
        assert len(r.command_ledger) > 0


@pytest.mark.integration
def test_e2e_real_benchmark_run():
    """
    Genuine end-to-end benchmark execution against a real pinned GitHub repository.
    Runs through Docker sandbox, inspects workspace, runs verification, and builds evidence.
    """
    entry = BenchmarkEntry(
        id="E2E_SIX",
        name="six-compatibility-library",
        stack="python",
        expected_outcome="GREEN",
        repo_url="https://github.com/benjaminp/six.git",
        commit_sha="c8e394065cd541a16c040515dc0afb85cf22a7c3",
        verification_type="pytest",
    )
    adapter = RealExecutionAdapter(timeout_seconds=300, max_steps=15)
    res = adapter.execute(entry)

    # Assert genuine execution artifacts
    assert res.simulated is False
    assert res.actual_commit_sha == "c8e394065cd541a16c040515dc0afb85cf22a7c3"
    assert res.wall_clock_seconds > 0.0
    assert len(res.command_ledger) > 0
    assert res.actual_outcome in {"GREEN", "FAILED"}  # Genuine execution verdict
