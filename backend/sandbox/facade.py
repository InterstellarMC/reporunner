"""ExecutionService facade: the primary execution interface."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from backend.sandbox.api.schemas import (
    CommandRequest,
    ExecutionResult,
    PatchProposal,
    PatchResult,
    PatchStatus,
    RollbackResult,
    RunLimits,
)
from backend.sandbox.exec.background import BackgroundProcessManager
from backend.sandbox.exec.engine import CommandEngine
from backend.sandbox.exec.limits import BudgetTracker
from backend.sandbox.patch.applier import PatchApplier
from backend.sandbox.patch.history import PatchHistory
from backend.sandbox.patch.rollback import PatchRollbackService
from backend.sandbox.patch.validator import PatchValidator
from backend.sandbox.sandbox_manager.manager import SandboxManager
from backend.sandbox.workspace.manager import WorkspaceManager

logger = logging.getLogger(__name__)


class ExecutionService:
    """The unified Execution and Verification service facade."""

    def __init__(self, run_id: str, limits: Optional[RunLimits] = None, base_image: str = "python:3.11-slim"):
        self.run_id = run_id
        self.limits = limits or RunLimits()
        self.base_image = base_image

        self.sandbox = SandboxManager(run_id=run_id, base_image=base_image, limits=self.limits)
        self.budget = BudgetTracker(run_id=run_id, limits=self.limits)
        self.engine = CommandEngine(sandbox=self.sandbox, budget=self.budget)
        self.workspace = WorkspaceManager(sandbox=self.sandbox)
        self.bg_process = BackgroundProcessManager(sandbox=self.sandbox)

        self.patch_history = PatchHistory(run_id=run_id)
        self.patch_applier = PatchApplier(sandbox=self.sandbox, history=self.patch_history)
        self.patch_rollback = PatchRollbackService(sandbox=self.sandbox)

        self.state = "CREATED"
        self.pinned_sha: Optional[str] = None
        self.current_snapshot_id: str = "init"

    def provision_run(self, repo_url: Optional[str] = None, commit_sha: Optional[str] = None) -> Dict[str, Any]:
        """Provision sandbox container and clone repository into workspace volume."""
        logger.info("Provisioning run %s with image %s", self.run_id, self.base_image)
        self.sandbox.start()
        self.state = "PROVISIONED"

        if repo_url:
            success, sha_or_err = self.workspace.clone_repository(repo_url=repo_url, commit_sha=commit_sha)
            if not success:
                logger.error("Failed to clone repository: %s", sha_or_err)
                raise RuntimeError(sha_or_err)
            self.pinned_sha = sha_or_err
            if self.workspace.current_snapshot_id:
                self.current_snapshot_id = self.workspace.current_snapshot_id
        else:
            # Create initial placeholder snapshot in empty workspace
            self.current_snapshot_id = "init_snap"
            self.patch_applier._save_shadow_snapshot(self.current_snapshot_id)

        self.state = "ACTIVE"
        return {
            "run_id": self.run_id,
            "pinned_sha": self.pinned_sha,
            "initial_snapshot_id": self.current_snapshot_id,
            "state": self.state,
        }

    def run_command(self, req: CommandRequest) -> ExecutionResult:
        """Run repository command through the single choke point."""
        return self.engine.execute(req)

    def validate_patch(self, proposal: PatchProposal) -> Optional[PatchResult]:
        """Validate patch proposal without applying it."""
        return PatchValidator.validate(
            proposal=proposal,
            current_snapshot_id=self.current_snapshot_id,
            run_is_active=(self.state == "ACTIVE"),
        )

    def apply_patch(self, proposal: PatchProposal) -> PatchResult:
        """Apply patch atomically; updates current_snapshot_id on success."""
        res = self.patch_applier.apply(
            proposal=proposal,
            current_snapshot_id=self.current_snapshot_id,
            is_active=(self.state == "ACTIVE"),
        )
        if res.status == PatchStatus.APPLIED and res.new_snapshot_id:
            self.current_snapshot_id = res.new_snapshot_id
        return res

    def rollback(self, target_snapshot_id: str) -> RollbackResult:
        """Roll back workspace to target snapshot ID."""
        res = self.patch_rollback.rollback(self.run_id, target_snapshot_id)
        if res.status == "SUCCESS":
            self.current_snapshot_id = target_snapshot_id
        return res

    def start_process(
        self,
        process_id: str,
        cmd: str,
        port: Optional[int] = None,
        path: str = "/",
        timeout_s: int = 30,
    ) -> Tuple[bool, str]:
        """Start long-running server in background."""
        return self.bg_process.start_process(
            process_id=process_id,
            cmd=cmd,
            readiness_port=port,
            readiness_path=path,
            timeout_s=timeout_s,
        )

    def stop_process(self, process_id: str) -> None:
        """Stop background process."""
        self.bg_process.stop_process(process_id)

    def get_run_state(self) -> Dict[str, Any]:
        """Query sandbox, workspace and budget state."""
        return {
            "run_id": self.run_id,
            "state": self.state,
            "steps_used": self.budget.step_count,
            "steps_limit": self.limits.max_steps,
            "elapsed_seconds": round(self.budget.elapsed_seconds, 2),
            "command_ledger_len": len(self.engine.command_ledger),
            "pinned_sha": self.pinned_sha,
            "current_snapshot_id": self.current_snapshot_id,
        }

    def close_run(self) -> None:
        """Clean up all sandbox and background process resources."""
        self.bg_process.stop_all()
        self.sandbox.stop()
        self.budget.close()
        self.state = "CLOSED"
