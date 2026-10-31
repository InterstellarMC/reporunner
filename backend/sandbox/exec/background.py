"""Background process supervision and readiness probing."""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple
from backend.sandbox.sandbox_manager.manager import SandboxManager

logger = logging.getLogger(__name__)


class BackgroundProcessManager:
    """Manages long-running processes (e.g. web servers) inside the sandbox."""

    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox
        self.processes: Dict[str, dict] = {}

    def start_process(
        self,
        process_id: str,
        cmd: str,
        log_file: str = "/tmp/bg_process.log",
        readiness_port: Optional[int] = None,
        readiness_path: str = "/",
        timeout_s: int = 30,
    ) -> Tuple[bool, str]:
        """
        Start process in background, logging to log_file.
        Optionally waits for readiness probe on readiness_port.
        """
        # Run command with nohup in background, redirecting stdout/stderr to log_file
        bg_cmd = f"nohup sh -c '{cmd}' > {log_file} 2>&1 & echo $!"
        exit_code, out, _ = self.sandbox.exec_in_sandbox(bg_cmd, timeout_s=10)
        pid_str = out.decode("utf-8").strip()

        if exit_code != 0:
            return False, f"Failed to start background process: {pid_str}"

        self.processes[process_id] = {
            "pid": pid_str,
            "cmd": cmd,
            "log_file": log_file,
            "port": readiness_port,
        }

        # If readiness probe requested, poll port or HTTP
        if readiness_port:
            ready = self._wait_for_port(readiness_port, readiness_path, timeout_s)
            if not ready:
                tail = self.get_process_log_tail(process_id, lines=50)
                return False, f"Process started (PID {pid_str}) but failed readiness probe on port {readiness_port}. Logs:\n{tail}"

        return True, f"Process started with PID {pid_str}"

    def _wait_for_port(self, port: int, path: str = "/", timeout_s: int = 30) -> bool:
        """Probe localhost port inside the container until HTTP/TCP connects or timeout."""
        probe_cmd = (
            f"python3 -c \""
            f"import socket, time, urllib.request; "
            f"start = time.time(); "
            f"ready = False; "
            f"while time.time() - start < {timeout_s}: "
            f"    try: "
            f"        resp = urllib.request.urlopen('http://127.0.0.1:{port}{path}', timeout=2); "
            f"        if resp.status < 500: "
            f"            ready = True; break; "
            f"    except Exception: "
            f"        pass; "
            f"    time.sleep(0.5); "
            f"exit(0 if ready else 1)\""
        )
        code, _, _ = self.sandbox.exec_in_sandbox(probe_cmd, timeout_s=timeout_s + 5)
        return code == 0

    def get_process_log_tail(self, process_id: str, lines: int = 100) -> str:
        """Fetch the last N lines from the process log file."""
        proc = self.processes.get(process_id)
        if not proc:
            return ""
        log_file = proc["log_file"]
        code, out, _ = self.sandbox.exec_in_sandbox(f"tail -n {lines} {log_file} 2>/dev/null", timeout_s=5)
        return out.decode("utf-8", errors="replace")

    def stop_process(self, process_id: str) -> None:
        """Terminate background process and child processes."""
        proc = self.processes.pop(process_id, None)
        if proc and proc.get("pid"):
            pid = proc["pid"]
            self.sandbox.exec_in_sandbox(f"kill -TERM -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null || true", timeout_s=5)
            time.sleep(1)
            self.sandbox.exec_in_sandbox(f"kill -KILL -{pid} 2>/dev/null || kill -KILL {pid} 2>/dev/null || true", timeout_s=5)

    def stop_all(self) -> None:
        """Stop all supervised background processes."""
        for pid_key in list(self.processes.keys()):
            self.stop_process(pid_key)
