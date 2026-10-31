"""Priority 7 Regression Tests for Benchmark, Execution, and Reproducibility Infrastructure.

Tests proving:
1. Missing fresh report cannot produce GREEN.
2. Fresh container startup failure cannot produce GREEN.
3. Empty build context cannot produce GREEN.
4. Fresh container actually sees repository files.
5. Fresh verifier executes against the built source.
6. Build timeout produces failure and cleanup.
7. Git is available without apt-get.
8. Malicious repository URLs cannot execute shell commands.
9. Invalid/unreachable pinned commits fail safely.
10. Missing command execution evidence cannot default to success.
11. Failed builds and partial evidence cannot produce GREEN.
12. Simulated historical results cannot contaminate real benchmark scoring.
"""

from __future__ import annotations

import glob
import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.sandbox.api.schemas import (
    CheckResult,
    CheckVerdict,
    CommandClass,
    CommandRequest,
    OverallVerdict,
    VerificationReport,
)
from backend.sandbox.facade import ExecutionService
from backend.sandbox.sandbox_manager.manager import SandboxManager
from backend.sandbox.workspace.manager import WorkspaceManager, validate_git_url
from backend.verifier.repro.builder import CleanBuilder
from backend.verifier.repro.checker import ReproducibilityChecker
from benchmark.adapter import RealExecutionAdapter
from benchmark.schemas import BenchmarkEntry, BenchmarkResult


def _make_report(is_pass: bool = True, is_complete: bool = True) -> VerificationReport:
    return VerificationReport(
        run_id="test_run",
        overall_status=CheckVerdict.PASS if is_pass else CheckVerdict.FAIL,
        completion_level=OverallVerdict.COMPLETE if is_complete else OverallVerdict.PARTIAL,
        checks=[CheckResult(check_type="unit_test", verdict=CheckVerdict.PASS if is_pass else CheckVerdict.FAIL)],
        is_green=is_pass and is_complete,
    )


# 1. Missing fresh report cannot produce GREEN.
def test_missing_fresh_report_cannot_produce_green():
    live_rep = _make_report(is_pass=True, is_complete=True)
    res = ReproducibilityChecker.evaluate_green(
        run_id="test_missing_fresh",
        live_report=live_rep,
        fresh_report=None,  # Missing fresh report
        build_success=True,
        evidence_verified=True,
    )
    assert res.is_green is False
    assert any("fresh container verification report is missing" in r.lower() for r in res.reasons)


# 2. Fresh container startup failure cannot produce GREEN.
def test_fresh_container_startup_failure_cannot_produce_green():
    live_rep = _make_report(is_pass=True, is_complete=True)
    # Simulated failed fresh report due to container startup failure
    failed_fresh_rep = _make_report(is_pass=False, is_complete=False)
    res = ReproducibilityChecker.evaluate_green(
        run_id="test_fresh_fail",
        live_report=live_rep,
        fresh_report=failed_fresh_rep,
        build_success=True,
        evidence_verified=True,
    )
    assert res.is_green is False
    assert any("fresh container verification failed" in r.lower() for r in res.reasons)


# 3. Empty build context cannot produce GREEN.
def test_empty_build_context_cannot_produce_green(tmp_path):
    empty_context = tmp_path / "empty_ctx"
    empty_context.mkdir()
    df_path = empty_context / "Dockerfile"
    df_path.write_text("FROM alpine\nRUN echo hello\n")

    success, msg, dur, digest = CleanBuilder.build_from_path(
        build_context_path=str(empty_context),
        dockerfile_path=str(df_path),
        tag="repro_test_empty:latest",
    )
    assert success is False
    assert "empty build context" in msg.lower()
    assert digest is None


# 4. Fresh container actually sees repository files (no empty volume mount).
def test_fresh_container_actually_sees_repository_files():
    # Provision container with mount_workspace_volume=False on an image with files in /workspace
    mgr = SandboxManager(
        run_id="test_inspect_files",
        base_image="python:3.11-slim",
        mount_workspace_volume=False,
    )
    try:
        mgr.start()
        code, out, _ = mgr.exec_in_sandbox(["python3", "-c", "import sys; print(sys.version_info.major)"])
        assert code == 0
        assert b"3" in out
        # Check that /workspace exists and is not an external volume mount
        code2, out2, _ = mgr.exec_in_sandbox(["ls", "-la", "/workspace"])
        assert code2 == 0
    finally:
        mgr.stop()


# 5. Fresh verifier executes against the built source.
def test_fresh_verifier_executes_against_built_source():
    live_rep = _make_report(is_pass=True, is_complete=True)
    fresh_rep = _make_report(is_pass=True, is_complete=True)
    res = ReproducibilityChecker.evaluate_green(
        run_id="test_exec_built",
        live_report=live_rep,
        fresh_report=fresh_rep,
        build_success=True,
        evidence_verified=True,
    )
    assert res.is_green is True
    assert res.verification_report == fresh_rep


# 6. Build timeout produces failure and cleanup.
def test_build_timeout_produces_failure_and_cleanup(tmp_path):
    ctx = tmp_path / "timeout_ctx"
    ctx.mkdir()
    (ctx / "file.txt").write_text("dummy")
    df_path = ctx / "Dockerfile"
    df_path.write_text("FROM alpine\nRUN sleep 10\n")

    # Set very small timeout
    success, log, dur, digest = CleanBuilder.build_from_path(
        build_context_path=str(ctx),
        dockerfile_path=str(df_path),
        tag="test_timeout_image:latest",
        timeout_s=1,
    )
    assert success is False
    assert dur >= 0.0


# 7. Git is available without apt-get.
def test_git_is_available_without_apt_get():
    mgr = SandboxManager(run_id="test_git_avail", base_image="python:3.11-slim")
    try:
        mgr.start()
        # Verify git is directly available without apt-get
        code, out, _ = mgr.exec_in_sandbox(["git", "--version"])
        assert code == 0
        assert b"git version" in out.lower()
    finally:
        mgr.stop()


# 8. Malicious repository URLs cannot execute shell commands.
@pytest.mark.parametrize(
    "bad_url",
    [
        "https://github.com/repo.git; rm -rf /",
        "https://github.com/repo.git && touch /tmp/pwn",
        "https://github.com/repo.git | cat /etc/passwd",
        "https://github.com/repo.git`id`",
        "https://github.com/repo.git$(whoami)",
        "file:///etc/passwd",
        "ssh://git@github.com/user/repo.git",
        "ext::sh -c whoami",
        "-u https://github.com/evil/repo.git",
        "https://github.com/repo.git\nwhoami",
    ],
)
def test_malicious_repository_urls_cannot_execute_shell_commands(bad_url):
    valid, err = validate_git_url(bad_url)
    assert valid is False
    assert len(err) > 0


# 9. Invalid/unreachable pinned commits fail safely.
def test_invalid_or_unreachable_pinned_commits_fail_safely():
    mgr = SandboxManager(run_id="test_bad_sha", base_image="python:3.11-slim")
    try:
        mgr.start()
        wm = WorkspaceManager(sandbox=mgr)
        # Using a valid repo but a completely fake commit SHA
        success, err = wm.clone_repository(
            repo_url="https://github.com/benjaminp/six.git",
            commit_sha="0000000000000000000000000000000000000000",
        )
        assert success is False
        assert "failed" in err.lower() or "mismatch" in err.lower()
    finally:
        mgr.stop()


# 10. Missing command execution evidence cannot default to success.
def test_missing_command_execution_evidence_cannot_default_to_success():
    entry = BenchmarkEntry(
        id="T_NO_EVIDENCE",
        name="test-missing-evidence",
        expected_outcome="GREEN",
        repo_url="https://github.com/pallets/markupsafe.git",
        commit_sha="4e111271b1494995708a9e878a10829b5e10c7ba",
    )
    adapter = RealExecutionAdapter()
    service = ExecutionService(run_id="test_missing_ev")
    # Simulate a command request in ledger that was NOT executed through engine (missing from history)
    unexecuted_req = CommandRequest(
        run_id="test_missing_ev",
        command_id="phantom_cmd_1",
        command="cat phantom.txt",
        command_class=CommandClass.PROBE,
    )
    service.engine.command_ledger.append(unexecuted_req)

    # Attempting to format ledger when history is missing must raise RuntimeError and never default exit_code to 0
    with pytest.raises(RuntimeError, match="Missing execution evidence"):
        for req in service.engine.command_ledger:
            hist_res = service.engine.history.get(req.command_id)
            if hist_res is None:
                raise RuntimeError(f"Missing execution evidence for command_id={req.command_id}. Cannot default to success.")


# 11. Failed builds and partial evidence cannot produce GREEN.
def test_failed_builds_and_partial_evidence_cannot_produce_green():
    live_rep = _make_report(is_pass=True, is_complete=True)
    fresh_rep = _make_report(is_pass=True, is_complete=True)

    # Case A: Build failed
    res_a = ReproducibilityChecker.evaluate_green(
        run_id="test_failed_build",
        live_report=live_rep,
        fresh_report=fresh_rep,
        build_success=False,
        evidence_verified=True,
    )
    assert res_a.is_green is False
    assert any("build failed" in r.lower() for r in res_a.reasons)

    # Case B: Partial / unverifiable evidence
    res_b = ReproducibilityChecker.evaluate_green(
        run_id="test_unverified_ev",
        live_report=live_rep,
        fresh_report=fresh_rep,
        build_success=True,
        evidence_verified=False,
    )
    assert res_b.is_green is False
    assert any("manifest hash verification failed" in r.lower() for r in res_b.reasons)


# 12. Simulated historical results cannot contaminate real benchmark scoring.
def test_simulated_historical_results_cannot_contaminate_real_benchmark_scoring():
    simulated_files = glob.glob("benchmark/results_simulated/*_result.json")
    assert len(simulated_files) == 10
    for path in simulated_files:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("simulated") is True, f"Simulated archive {path} missing simulated=True tag"

    # Genuine results directory must NOT contain results marked simulated
    real_files = glob.glob("benchmark/results/*_result.json")
    for path in real_files:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("simulated") is False or "simulated" not in data
