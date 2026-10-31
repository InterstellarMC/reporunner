"""Docker container lifecycle, command timeouts, output truncation, and cleanup."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from backend.sandbox.api.schemas import CommandClass, CommandRequest, ExecutionResult, ExecutionStatus, RunLimits
from backend.sandbox.facade import ExecutionService
from backend.sandbox.sandbox_manager.manager import SandboxManager


class Sandbox:
    """Convenience wrapper for reporunner agent loop."""

    def __init__(self, run_id: str = "default_run", base_image: str = "python:3.11-slim"):
        self.service = ExecutionService(run_id=run_id, base_image=base_image)
        self._cmd_counter = 0

    def start(self) -> None:
        self.service.provision_run()

    def run_cmd(
        self,
        cmd: str,
        timeout_s: int = 120,
        env: Optional[Dict[str, str]] = None,
        cwd: str = "/workspace",
    ) -> Dict[str, Any]:
        """
        Run command in sandbox conforming to AGENTS.md tool definition:
        run_cmd(cmd: str, timeout_s: int=120) -> {exit_code, stdout_tail, stderr_tail}
        """
        self._cmd_counter += 1
        req = CommandRequest(
            run_id=self.service.run_id,
            command_id=f"cmd_{self._cmd_counter}",
            command=cmd,
            cwd=cwd,
            timeout_s=timeout_s,
            command_class=CommandClass.RUN,
            env=env or {},
        )
        res: ExecutionResult = self.service.run_command(req)
        return {
            "exit_code": res.exit_code,
            "stdout_tail": res.stdout,
            "stderr_tail": res.stderr,
            "duration": res.duration,
            "status": res.status.value,
            "truncated": res.truncated,
        }

    def close(self) -> None:
        self.service.close_run()
