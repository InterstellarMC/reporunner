"""Feedback generator that turns raw execution output into structured ExecutionResult."""

from __future__ import annotations

import time
from typing import Optional

from backend.sandbox.api.schemas import (
    CommandRequest,
    ErrorCategory,
    ExecutionResult,
    ExecutionStatus,
)
from backend.sandbox.feedback.categories import classify_error
from backend.sandbox.feedback.fingerprint import compute_fingerprint
from backend.sandbox.exec.output import process_output


def build_execution_result(
    req: CommandRequest,
    exit_code: int,
    raw_output: str,
    duration: float,
    snapshot_id: Optional[str] = None,
    started_at: Optional[str] = None,
) -> ExecutionResult:
    """Build standardized ExecutionResult from execution details."""
    secrets = [req.env[k] for k in req.secret_keys if k in req.env]

    stdout_text = raw_output if exit_code == 0 else ""
    stderr_text = "" if exit_code == 0 else raw_output

    clean_out, clean_err, is_truncated = process_output(
        stdout=stdout_text,
        stderr=stderr_text,
        secrets=secrets,
    )

    # Classify error category mechanically
    category = classify_error(exit_code=exit_code, stderr=clean_err, stdout=clean_out)

    # Compute deterministic fingerprint if failed
    raw_for_fingerprint = clean_err if clean_err else clean_out
    fingerprint = compute_fingerprint(category.value, raw_for_fingerprint) if exit_code != 0 else None

    # Status
    if exit_code == 0:
        status = ExecutionStatus.SUCCESS
    elif category == ErrorCategory.TIMEOUT:
        status = ExecutionStatus.TIMEOUT
    elif category == ErrorCategory.OOM:
        status = ExecutionStatus.OOM_KILLED
    else:
        status = ExecutionStatus.FAILED

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    return ExecutionResult(
        run_id=req.run_id,
        command_id=req.command_id,
        exit_code=exit_code,
        stdout=clean_out,
        stderr=clean_err,
        duration=round(duration, 3),
        status=status,
        error_category=category,
        fingerprint=fingerprint,
        truncated=is_truncated,
        snapshot_id=snapshot_id,
        started_at=started_at or now_iso,
        ended_at=now_iso,
    )
