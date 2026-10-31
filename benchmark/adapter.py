"""Documented Execution Adapter for the real RepoRunner execution pipeline."""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional, Protocol, Tuple

from backend.sandbox.api.schemas import (
    CheckVerdict,
    CommandClass,
    CommandRequest,
    ErrorCategory,
    OverallVerdict,
    RunLimits,
    VerificationReport,
)
from backend.sandbox.facade import ExecutionService
from backend.sandbox.sandbox_manager.docker_client import get_docker_client
from backend.sandbox.sandbox_manager.profiles import (
    REPORUNNER_BASE_IMAGE_NODE,
    REPORUNNER_BASE_IMAGE_PYTHON,
    get_base_image_digest,
)
from backend.verifier.repro.builder import CleanBuilder
from backend.verifier.repro.checker import ReproducibilityChecker
from benchmark.schemas import BenchmarkEntry, BenchmarkResult

logger = logging.getLogger(__name__)

# Patterns for observed blocker detection
SECRET_PATTERNS = [
    re.compile(r"KeyError:\s*['\"]([A-Z0-9_]*(?:KEY|SECRET|TOKEN|API)[A-Z0-9_]*)['\"]", re.IGNORECASE),
    re.compile(r"(OPENAI_API_KEY|ANTHROPIC_API_KEY|AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN)", re.IGNORECASE),
    re.compile(r"(missing|requires?|set)\s+(the\s+)?(api[_\s-]?key|secret[_\s-]?key|access[_\s-]?token)", re.IGNORECASE),
    re.compile(r"AuthenticationError|MissingAPIKeyError|No API key provided", re.IGNORECASE),
    re.compile(r"api_key\s*(?:client|option)?\s*must be provided", re.IGNORECASE),
]

HARDWARE_PATTERNS = [
    re.compile(r"Torch not compiled with CUDA enabled", re.IGNORECASE),
    re.compile(r"No CUDA GPUs are available|CUDA error|Found no NVIDIA driver", re.IGNORECASE),
    re.compile(r"device ordinal|cuda.*not available|torch\.cuda\.is_available\(\) is False", re.IGNORECASE),
    re.compile(r"AssertionError:.*cuda", re.IGNORECASE),
]

PACKAGE_PATTERNS = [
    re.compile(r"Permission denied \(publickey\)", re.IGNORECASE),
    re.compile(r"fatal: clone of ['\"][^'\"]+['\"] failed", re.IGNORECASE),
    re.compile(r"npm ERR! 404 Not Found", re.IGNORECASE),
    re.compile(r"npm ERR! code (?:E401|E403|E404)", re.IGNORECASE),
    re.compile(r"inaccessible.*submodule", re.IGNORECASE),
    re.compile(r"Could not find a version that satisfies the requirement.*(?:internal|private|billing)", re.IGNORECASE),
]


class ExecutionAdapter(Protocol):
    """Protocol for benchmark execution adapters."""

    def execute(self, entry: BenchmarkEntry) -> BenchmarkResult:
        """Execute a benchmark entry and return a comprehensive result."""
        ...


class RealExecutionAdapter:
    """
    Genuine execution adapter connecting the benchmark harness directly to
    the ExecutionService facade, Docker sandbox, independent verifier, and
    fresh-build reproducibility pipeline.
    """

    def __init__(self, timeout_seconds: int = 600, max_steps: int = 25):
        self.timeout_seconds = timeout_seconds
        self.max_steps = max_steps

    def execute(self, entry: BenchmarkEntry) -> BenchmarkResult:
        start_time = time.monotonic()
        run_id = f"bench_{entry.id}_{uuid.uuid4().hex[:8]}"
        base_image = REPORUNNER_BASE_IMAGE_NODE if entry.stack == "node" else REPORUNNER_BASE_IMAGE_PYTHON
        limits = RunLimits(max_steps=self.max_steps, max_seconds=self.timeout_seconds)

        service = ExecutionService(run_id=run_id, limits=limits, base_image=base_image)
        actual_sha: Optional[str] = None
        command_ledger_records: List[Dict[str, Any]] = []
        actual_outcome = "FAILED"
        failure_reason: Optional[str] = None
        blocker_reason: Optional[str] = None
        verification_report_dict: Optional[Dict[str, Any]] = None
        fresh_build_dict: Optional[Dict[str, Any]] = None
        fresh_verif_dict: Optional[Dict[str, Any]] = None
        repro_verdict: Optional[bool] = None
        evidence_dir: Optional[str] = None

        try:
            # Step 1: Provision sandbox and clone repository
            logger.info("Provisioning benchmark %s (URL=%s, SHA=%s)", entry.id, entry.repo_url, entry.commit_sha)
            try:
                prov = service.provision_run(repo_url=entry.repo_url, commit_sha=entry.commit_sha)
                actual_sha = prov.get("pinned_sha")
            except Exception as clone_err:
                logger.error("Clone failed for %s: %s", entry.id, clone_err)
                elapsed = time.monotonic() - start_time
                return BenchmarkResult(
                    entry_id=entry.id,
                    repo_url=entry.repo_url,
                    expected_commit_sha=entry.commit_sha,
                    actual_commit_sha=None,
                    expected_outcome=entry.expected_outcome,
                    actual_outcome="CLONE_FAILED",
                    match=(entry.expected_outcome == "CLONE_FAILED"),
                    execution_steps=0,
                    wall_clock_duration=round(elapsed, 3),
                    wall_clock_seconds=round(elapsed, 3),
                    command_ledger=[],
                    patch_history=[],
                    failure_reason=f"Repository clone/checkout failed: {clone_err}",
                    simulated=False,
                    details=f"Clone failed: {clone_err}",
                )

            # Step 2: Enforce commit SHA equality
            if actual_sha and entry.commit_sha and not actual_sha.lower().startswith(entry.commit_sha.lower()):
                elapsed = time.monotonic() - start_time
                return BenchmarkResult(
                    entry_id=entry.id,
                    repo_url=entry.repo_url,
                    expected_commit_sha=entry.commit_sha,
                    actual_commit_sha=actual_sha,
                    expected_outcome=entry.expected_outcome,
                    actual_outcome="COMMIT_MISMATCH",
                    match=False,
                    execution_steps=1,
                    wall_clock_duration=round(elapsed, 3),
                    wall_clock_seconds=round(elapsed, 3),
                    command_ledger=[],
                    patch_history=[],
                    failure_reason=f"Checked out SHA {actual_sha} does not match expected {entry.commit_sha}",
                    simulated=False,
                    details="Commit mismatch",
                )

            # Step 3: Inspect workspace and run dependency setup
            detected_blocker = self._run_setup_and_detect_blockers(service, entry, command_ledger_records)
            if detected_blocker:
                actual_outcome = "BLOCKED"
                blocker_reason = detected_blocker
            else:
                # Step 4: Run independent verification in the live sandbox
                logger.info("Executing independent verifier for %s", entry.id)
                live_report = self._run_verification(service, entry)
                verification_report_dict = live_report.model_dump()

                if (
                    live_report.overall_status == CheckVerdict.PASS
                    and live_report.completion_level == OverallVerdict.COMPLETE
                ):
                    # Step 5: Reproducibility check - Clean image build & fresh container verification
                    logger.info("Live verification PASSED for %s. Running reproducibility check.", entry.id)
                    repro_ok, repro_details = self._run_reproducibility(service, entry, live_report)
                    fresh_build_dict = repro_details.get("fresh_build")
                    fresh_verif_dict = repro_details.get("fresh_verification")
                    evidence_dir = repro_details.get("evidence_directory")
                    repro_verdict = repro_ok

                    if repro_ok:
                        actual_outcome = "GREEN"
                    else:
                        actual_outcome = "FAILED"
                        failure_reason = f"Reproducibility check failed: {repro_details.get('reasons')}"
                else:
                    actual_outcome = "FAILED"
                    failure_reason = f"Live verification failed: {live_report.failure_summary or 'Incomplete checks'}"

        except Exception as ex:
            logger.exception("Unexpected execution error in benchmark %s: %s", entry.id, ex)
            actual_outcome = "FAILED"
            failure_reason = f"Unexpected execution exception: {ex}"
        finally:
            service.close_run()

        elapsed = time.monotonic() - start_time

        # Strictly populate command ledger exit codes and durations from execution history
        ledger_data: List[Dict[str, Any]] = []
        for req in service.engine.command_ledger:
            hist_res = service.engine.history.get(req.command_id)
            if hist_res is None:
                raise RuntimeError(f"Missing execution evidence for command_id={req.command_id}. Cannot default to success.")
            ledger_data.append({
                "command_id": req.command_id,
                "command": req.command,
                "exit_code": hist_res.exit_code,
                "duration": hist_res.duration,
                "command_class": req.command_class.value,
                "status": hist_res.status.value,
                "error_category": hist_res.error_category.value,
            })

        return BenchmarkResult(
            entry_id=entry.id,
            repo_url=entry.repo_url,
            expected_commit_sha=entry.commit_sha,
            actual_commit_sha=actual_sha,
            expected_outcome=entry.expected_outcome,
            actual_outcome=actual_outcome,
            match=(actual_outcome == entry.expected_outcome),
            execution_steps=service.budget.step_count,
            wall_clock_duration=round(elapsed, 3),
            wall_clock_seconds=round(elapsed, 3),
            command_ledger=ledger_data,
            patch_history=list(service.patch_history.entries),
            verification_report=verification_report_dict,
            fresh_build_result=fresh_build_dict,
            fresh_container_verification_result=fresh_verif_dict,
            fresh_verification_result=fresh_verif_dict,
            reproducibility_verdict=repro_verdict,
            evidence_directory=evidence_dir,
            failure_reason=failure_reason,
            blocker_reason=blocker_reason,
            simulated=False,
            details=blocker_reason or failure_reason or "Verification and reproducibility passed",
        )

    def _run_setup_and_detect_blockers(
        self,
        service: ExecutionService,
        entry: BenchmarkEntry,
        ledger: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Execute setup commands inside sandbox and detect blockers from observed output."""
        find_req = CommandRequest(
            run_id=service.run_id,
            command_id=f"setup_inspect_{uuid.uuid4().hex[:6]}",
            command="ls -la /workspace",
            cwd="/workspace",
            timeout_s=30,
            command_class=CommandClass.PROBE,
        )
        res_find = service.run_command(find_req)
        ledger.append({"cmd": find_req.command, "exit": res_find.exit_code})

        setup_commands: List[Tuple[str, CommandClass]] = []
        if entry.stack == "python":
            if "requirements.txt" in res_find.stdout:
                setup_commands.append(("pip install --no-cache-dir -r requirements.txt", CommandClass.INSTALL))
            if "setup.py" in res_find.stdout or "pyproject.toml" in res_find.stdout:
                setup_commands.append(("pip install --no-cache-dir -e .", CommandClass.INSTALL))
        elif entry.stack == "node":
            if ".gitmodules" in res_find.stdout:
                setup_commands.append(("git submodule update --init --recursive", CommandClass.FETCH))
            if "package.json" in res_find.stdout:
                setup_commands.append(("npm install", CommandClass.INSTALL))

        if entry.cli_command:
            setup_commands.append((entry.cli_command, CommandClass.TEST))
        elif entry.fault and ("Requires" in entry.fault or "runtime" in entry.fault or "GPU" in entry.fault):
            if entry.stack == "python":
                setup_commands.append(("python3 -c 'import torch; print(torch.cuda.is_available()); assert torch.cuda.is_available()'", CommandClass.TEST))

        for cmd_str, cmd_class in setup_commands:
            req = CommandRequest(
                run_id=service.run_id,
                command_id=f"setup_{uuid.uuid4().hex[:6]}",
                command=cmd_str,
                cwd="/workspace",
                timeout_s=120,
                command_class=cmd_class,
            )
            res = service.run_command(req)
            ledger.append({"cmd": cmd_str, "exit": res.exit_code, "stdout": res.stdout, "stderr": res.stderr})

            combined_out = f"{res.stderr}\n{res.stdout}"

            for p in SECRET_PATTERNS:
                m = p.search(combined_out)
                if m:
                    return f"Observed missing credential/secret: {m.group(0)}"
            if res.error_category == ErrorCategory.ENV_VAR_MISSING:
                return f"Observed missing environment variable required for execution (exit {res.exit_code})"

            for p in HARDWARE_PATTERNS:
                m = p.search(combined_out)
                if m:
                    return f"Observed unavailable hardware requirement: NVIDIA CUDA GPU ({m.group(0)})"

            for p in PACKAGE_PATTERNS:
                m = p.search(combined_out)
                if m:
                    return f"Observed inaccessible dependency or private repository: {m.group(0)}"

        return None

    def _run_verification(self, service: ExecutionService, entry: BenchmarkEntry) -> VerificationReport:
        """Invoke the independent verifier through the documented facade."""
        if entry.verification_type == "http":
            return service.verify(http_port=entry.http_port or 5000)
        elif entry.verification_type == "cli":
            return service.verify(cli_command=entry.cli_command or "python3 -m unittest discover")
        else:
            return service.verify()

    def _run_reproducibility(
        self,
        service: ExecutionService,
        entry: BenchmarkEntry,
        live_report: VerificationReport,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Genuine reproducibility pipeline:
        1. Export exact /workspace source snapshot from container to build context.
        2. Verify source snapshot hash before building.
        3. Build clean image from the real repository source and generated Dockerfile.
        4. Start fresh container from the image without mounting an empty volume over /workspace.
        5. Execute independent verification against the built project.
        6. Reject GREEN if fresh verification fails or is incomplete.
        """
        details: Dict[str, Any] = {}
        build_context_dir = os.path.join("runs", service.run_id, "build_context")
        os.makedirs(build_context_dir, exist_ok=True)

        # 1. Export actual repository source snapshot from live sandbox
        source_hash = service.export_workspace(build_context_dir)
        details["source_snapshot_hash"] = source_hash

        # 2. Build evidence bundle including Dockerfile in evidence dir
        evidence_bundle = service.build_evidence(
            verification_report=live_report,
            run_cmd=entry.cli_command or "python3 -m unittest discover",
            port=entry.http_port,
            source_snapshot_hash=source_hash,
        )
        evidence_dir = evidence_bundle.get("evidence_dir")
        details["evidence_directory"] = evidence_dir

        # Copy generated Dockerfile into the build context
        src_dockerfile = os.path.join(evidence_dir, "Dockerfile")
        dst_dockerfile = os.path.join(build_context_dir, "Dockerfile")
        shutil.copy2(src_dockerfile, dst_dockerfile)

        # 3. Build clean image from real source context
        fresh_tag = f"repro_{service.run_id}:latest"
        build_success, build_log, build_dur, image_digest = CleanBuilder.build_from_path(
            build_context_path=build_context_dir,
            dockerfile_path=dst_dockerfile,
            tag=fresh_tag,
            timeout_s=300,
        )
        details["fresh_build"] = {
            "success": build_success,
            "duration_s": round(build_dur, 2),
            "image_tag": fresh_tag,
            "image_digest": image_digest,
        }

        if not build_success:
            details["reasons"] = [f"Fresh Docker build failed: {build_log[:200]}"]
            return False, details

        # 4. Start fresh container from built image WITHOUT mounting an empty volume over /workspace
        fresh_report: Optional[VerificationReport] = None
        fresh_service = ExecutionService(
            run_id=f"fresh_{service.run_id}",
            base_image=fresh_tag,
            mount_workspace_volume=False,  # DO NOT mount an empty volume over the built /workspace
        )
        try:
            fresh_service.sandbox.start()
            # 5. Execute independent verification against the actual built project
            fresh_report = self._run_verification(fresh_service, entry)
            details["fresh_verification"] = fresh_report.model_dump()
        except Exception as fresh_err:
            details["fresh_verification_error"] = str(fresh_err)
            details["reasons"] = [f"Fresh container startup/verification failed: {fresh_err}"]
            return False, details
        finally:
            fresh_service.close_run()
            # Clean up temporary fresh image and build context
            try:
                client = get_docker_client()
                client.images.remove(fresh_tag, force=True)
            except Exception:
                pass
            shutil.rmtree(build_context_dir, ignore_errors=True)

        # 6. Authoritative GREEN gate evaluation
        repro_report = ReproducibilityChecker.evaluate_green(
            run_id=service.run_id,
            live_report=live_report,
            fresh_report=fresh_report,
            build_success=build_success,
            build_duration_s=build_dur,
            evidence_verified=bool(evidence_bundle.get("manifest")),
        )
        details["reasons"] = repro_report.reasons
        return repro_report.is_green, details
