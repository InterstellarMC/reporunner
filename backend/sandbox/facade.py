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
from backend.sandbox.events.emitter import EventEmitter
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

    def __init__(
        self,
        run_id: str,
        limits: Optional[RunLimits] = None,
        base_image: str = "python:3.11-slim",
        mount_workspace_volume: bool = True,
        read_only_root: bool = False,
    ):
        self.run_id = run_id
        self.limits = limits or RunLimits()
        self.base_image = base_image
        self.mount_workspace_volume = mount_workspace_volume
        self.read_only_root = read_only_root

        self.emitter = EventEmitter(run_id=run_id)
        self.sandbox = SandboxManager(
            run_id=run_id,
            base_image=base_image,
            limits=self.limits,
            mount_workspace_volume=mount_workspace_volume,
            read_only_root=read_only_root,
        )
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
            self.current_snapshot_id = "init_snap"
            self.patch_applier._save_shadow_snapshot(self.current_snapshot_id)

        self.state = "ACTIVE"
        self.emitter.emit(
            "run.provisioned",
            {
                "run_id": self.run_id,
                "base_image": self.base_image,
                "pinned_sha": self.pinned_sha,
                "snapshot_id": self.current_snapshot_id,
            },
        )

        return {
            "run_id": self.run_id,
            "pinned_sha": self.pinned_sha,
            "initial_snapshot_id": self.current_snapshot_id,
            "state": self.state,
        }

    def run_command(self, req: CommandRequest) -> ExecutionResult:
        """Run repository command through the single choke point."""
        self.emitter.emit(
            "command.started",
            {
                "command_id": req.command_id,
                "command": req.command,
                "cwd": req.cwd,
                "timeout_s": req.timeout_s,
            },
        )
        res = self.engine.execute(req)
        self.emitter.emit(
            "command.finished",
            {
                "command_id": res.command_id,
                "exit_code": res.exit_code,
                "duration": res.duration,
                "status": res.status.value,
                "error_category": res.error_category.value,
                "fingerprint": res.fingerprint,
                "truncated": res.truncated,
            },
        )
        return res

    def validate_patch(self, proposal: PatchProposal) -> Optional[PatchResult]:
        """Validate patch proposal without applying it."""
        self.emitter.emit("patch.received", {"patch_id": proposal.patch_id, "action": "validate"})
        res = PatchValidator.validate(
            proposal=proposal,
            current_snapshot_id=self.current_snapshot_id,
            run_is_active=(self.state == "ACTIVE"),
        )
        if res:
            self.emitter.emit(
                "patch.rejected",
                {
                    "patch_id": proposal.patch_id,
                    "rejection_code": res.rejection_code.value if res.rejection_code else None,
                    "reason": res.rejection_reason,
                },
            )
        else:
            self.emitter.emit("patch.validated", {"patch_id": proposal.patch_id})
        return res

    def apply_patch(self, proposal: PatchProposal) -> PatchResult:
        """Apply patch atomically; updates current_snapshot_id on success."""
        self.emitter.emit("patch.received", {"patch_id": proposal.patch_id, "action": "apply"})
        res = self.patch_applier.apply(
            proposal=proposal,
            current_snapshot_id=self.current_snapshot_id,
            is_active=(self.state == "ACTIVE"),
        )
        if res.status == PatchStatus.APPLIED:
            if res.new_snapshot_id:
                self.current_snapshot_id = res.new_snapshot_id
            self.emitter.emit(
                "patch.applied",
                {
                    "patch_id": proposal.patch_id,
                    "new_snapshot_id": res.new_snapshot_id,
                    "changed_files": res.changed_files,
                },
            )
        else:
            self.emitter.emit(
                "patch.rejected",
                {
                    "patch_id": proposal.patch_id,
                    "rejection_code": res.rejection_code.value if res.rejection_code else None,
                    "reason": res.rejection_reason,
                },
            )
        return res

    def rollback(self, target_snapshot_id: str) -> RollbackResult:
        """Roll back workspace to target snapshot ID."""
        res = self.patch_rollback.rollback(self.run_id, target_snapshot_id)
        if res.status == "SUCCESS":
            self.current_snapshot_id = target_snapshot_id
            self.emitter.emit(
                "patch.rolled_back",
                {"target_snapshot_id": target_snapshot_id, "restored_snapshot_id": res.restored_snapshot_id},
            )
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
        success, msg = self.bg_process.start_process(
            process_id=process_id,
            cmd=cmd,
            readiness_port=port,
            readiness_path=path,
            timeout_s=timeout_s,
        )
        if success:
            self.emitter.emit("process.started", {"process_id": process_id, "port": port})
        return success, msg

    def stop_process(self, process_id: str) -> None:
        """Stop background process."""
        self.bg_process.stop_process(process_id)
        self.emitter.emit("process.stopped", {"process_id": process_id})

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

    def verify(
        self,
        entrypoint: Optional[str] = None,
        http_port: Optional[int] = None,
        http_path: str = "/",
        cli_command: Optional[str] = None,
        baseline_test_count: Optional[int] = None,
    ) -> VerificationReport:
        """Run independent rule-based verification checks over the workspace."""
        from backend.verifier.engine import VerificationEngine

        class _Adapter:
            def __init__(self, s: ExecutionService):
                self.s = s
                self.c = 0

            def run(self, cmd: str, timeout_s: int = 120, cwd: str = "/workspace") -> Tuple[int, str, str]:
                self.c += 1
                req = CommandRequest(
                    run_id=self.s.run_id,
                    command_id=f"verify_cmd_{self.c}",
                    command=cmd,
                    cwd=cwd,
                    timeout_s=timeout_s,
                )
                res = self.s.run_command(req)
                return res.exit_code, res.stdout, res.stderr

        engine = VerificationEngine(runner=_Adapter(self), sink=self.emitter)
        return engine.verify(
            run_id=self.run_id,
            entrypoint=entrypoint,
            http_port=http_port,
            http_path=http_path,
            cli_command=cli_command,
            baseline_test_count=baseline_test_count,
        )

    def export_workspace(self, target_dir: str) -> str:
        """Export exact /workspace snapshot from container to target directory and return tree hash."""
        return self.sandbox.export_workspace(target_dir)

    def build_evidence(
        self,
        verification_report: VerificationReport,
        run_cmd: str = "python3 app.py",
        port: Optional[int] = None,
        source_snapshot_hash: Optional[str] = None,
        image_digest: Optional[str] = None,
        build_logs: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate complete evidence bundle including Dockerfile, RUN.md, evidence.json and manifest."""
        from backend.evidence.generator import EvidenceGenerator
        gen = EvidenceGenerator(run_id=self.run_id)
        bundle = gen.generate_bundle(
            verification_report=verification_report,
            command_ledger=self.engine.command_ledger,
            base_image=self.base_image,
            run_cmd=run_cmd,
            port=port,
            pinned_sha=self.pinned_sha,
            source_snapshot_hash=source_snapshot_hash,
            image_digest=image_digest,
            build_logs=build_logs,
        )
        self.emitter.emit("evidence.ready", {"evidence_dir": bundle["evidence_dir"], "manifest": bundle["manifest"]})
        return bundle

    def reproduce(
        self,
        verification_report: VerificationReport,
        fresh_report: Optional[VerificationReport] = None,
        build_success: bool = True,
        build_duration_s: float = 0.0,
        run_cmd: str = "python3 app.py",
        port: Optional[int] = None,
    ) -> Any:
        """Run reproducibility check and the GREEN gate."""
        from backend.verifier.repro.checker import ReproducibilityChecker
        self.emitter.emit("repro.build_started", {"run_id": self.run_id})

        # Generate evidence bundle
        evidence = self.build_evidence(verification_report=verification_report, run_cmd=run_cmd, port=port)

        # Evaluate GREEN gate (fresh_report is mandatory for GREEN)
        repro_report = ReproducibilityChecker.evaluate_green(
            run_id=self.run_id,
            live_report=verification_report,
            fresh_report=fresh_report,
            build_success=build_success,
            build_duration_s=build_duration_s,
            evidence_verified=bool(evidence.get("manifest")),
        )

        self.emitter.emit(
            "repro.finished",
            {
                "run_id": self.run_id,
                "is_green": repro_report.is_green,
                "reasons": repro_report.reasons,
            },
        )
        return repro_report

    def close_run(self) -> None:
        """Clean up all sandbox and background process resources."""
        self.bg_process.stop_all()
        self.sandbox.stop()
        self.budget.close()
        self.state = "CLOSED"
        self.emitter.emit("run.closed", {"run_id": self.run_id})
