"""Command execution engine with policy, timeout, and idempotency."""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from backend.sandbox.api.schemas import (
    CommandRequest,
    ErrorCategory,
    ExecutionResult,
    ExecutionStatus,
)
from backend.sandbox.exec.limits import BudgetTracker
from backend.sandbox.exec.output import process_output
from backend.sandbox.exec.policy import check_command_policy
from backend.sandbox.sandbox_manager.manager import SandboxManager

logger = logging.getLogger(__name__)


class CommandEngine:
    """The authoritative choke point for executing commands inside the sandbox."""

    def __init__(self, sandbox: SandboxManager, budget: Optional[BudgetTracker] = None):
        self.sandbox = sandbox
        self.budget = budget or BudgetTracker(sandbox.run_id)
        self.history: Dict[str, ExecutionResult] = {}
        self.command_ledger: list[CommandRequest] = []

    def execute(self, req: CommandRequest) -> ExecutionResult:
        """
        Execute command with policy enforcement, idempotency, and output processing.
        """
        # 1. Idempotency check: if command_id already executed, return cached result
        if req.command_id in self.history:
            logger.info("Returning cached result for command_id=%s", req.command_id)
            return self.history[req.command_id]

        start_time_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 2. Budget / cap check
        budget_err = self.budget.check_budget()
        if budget_err:
            res = ExecutionResult(
                run_id=req.run_id,
                command_id=req.command_id,
                exit_code=-1,
                stdout="",
                stderr=budget_err,
                duration=0.0,
                status=ExecutionStatus.FAILED,
                error_category=ErrorCategory.TIMEOUT if "deadline" in budget_err else ErrorCategory.UNKNOWN,
                started_at=start_time_iso,
                ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self.history[req.command_id] = res
            return res

        # 3. Policy validation check
        allowed, policy_violation = check_command_policy(req.command)
        if not allowed:
            res = ExecutionResult(
                run_id=req.run_id,
                command_id=req.command_id,
                exit_code=-1,
                stdout="",
                stderr=f"Security policy violation: {policy_violation}",
                duration=0.0,
                status=ExecutionStatus.BLOCKED_POLICY,
                error_category=ErrorCategory.PERMISSION_DENIED,
                started_at=start_time_iso,
                ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self.history[req.command_id] = res
            return res

        # Record step and log command to ledger
        self.budget.record_step()
        self.command_ledger.append(req)

        # 4. Execute inside sandbox container
        secrets = [req.env[k] for k in req.secret_keys if k in req.env]

        try:
            exit_code, raw_output, duration = self.sandbox.exec_in_sandbox(
                cmd=req.command,
                workdir=req.cwd,
                env=req.env,
                timeout_s=req.timeout_s,
            )

            # Output decoding and stream processing
            decoded_text = raw_output.decode("utf-8", errors="replace")
            # If exit_code is 0, treat as stdout, else separate or combined
            stdout_text = decoded_text if exit_code == 0 else ""
            stderr_text = "" if exit_code == 0 else decoded_text

            clean_out, clean_err, is_truncated = process_output(
                stdout=stdout_text,
                stderr=stderr_text,
                secrets=secrets,
            )

            # Determine execution status
            if exit_code == 0:
                status = ExecutionStatus.SUCCESS
                err_category = ErrorCategory.NONE
            elif "timed out" in decoded_text.lower():
                status = ExecutionStatus.TIMEOUT
                err_category = ErrorCategory.TIMEOUT
            else:
                status = ExecutionStatus.FAILED
                err_category = ErrorCategory.UNKNOWN

            res = ExecutionResult(
                run_id=req.run_id,
                command_id=req.command_id,
                exit_code=exit_code,
                stdout=clean_out,
                stderr=clean_err,
                duration=round(duration, 3),
                status=status,
                error_category=err_category,
                truncated=is_truncated,
                started_at=start_time_iso,
                ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

        except Exception as e:
            res = ExecutionResult(
                run_id=req.run_id,
                command_id=req.command_id,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration=0.0,
                status=ExecutionStatus.SANDBOX_ERROR,
                error_category=ErrorCategory.UNKNOWN,
                started_at=start_time_iso,
                ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

        self.history[req.command_id] = res
        return res
