"""Workspace manager for repository cloning and snapshot tracking."""

from __future__ import annotations

import logging
from typing import Optional, Tuple
from backend.sandbox.sandbox_manager.manager import SandboxManager

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Handles git cloning and workspace state inside the sandbox."""

    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox
        self.pinned_sha: Optional[str] = None
        self.current_snapshot_id: Optional[str] = None

    def clone_repository(
        self,
        repo_url: str,
        commit_sha: Optional[str] = None,
        depth: int = 1,
    ) -> Tuple[bool, str]:
        """
        Clone the repository into /workspace.
        Returns (success, message_or_sha).
        """
        # Ensure git is installed and clone shallow
        clone_cmd = (
            f"if ! command -v git >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq git; fi; "
            f"git clone --depth {depth} '{repo_url}' /workspace 2>&1 || (rm -rf /workspace/* /workspace/.* 2>/dev/null; git clone '{repo_url}' /workspace 2>&1)"
        )
        exit_code, out, dur = self.sandbox.exec_in_sandbox(clone_cmd, user="0", timeout_s=180)
        if exit_code != 0:
            return False, f"Git clone failed (exit {exit_code}): {out.decode('utf-8', errors='replace')}"

        # If commit SHA specified, checkout
        if commit_sha:
            checkout_cmd = f"cd /workspace && git checkout '{commit_sha}'"
            c_code, c_out, _ = self.sandbox.exec_in_sandbox(checkout_cmd, timeout_s=30)
            if c_code != 0:
                return False, f"Git checkout {commit_sha} failed: {c_out.decode('utf-8', errors='replace')}"

        # Get current HEAD sha
        sha_cmd = "cd /workspace && git rev-parse HEAD"
        _, sha_out, _ = self.sandbox.exec_in_sandbox(sha_cmd, timeout_s=10)
        self.pinned_sha = sha_out.decode("utf-8").strip()

        # Compute initial snapshot hash
        snap_cmd = (
            "python3 -c \""
            "import os, hashlib; "
            "h = hashlib.sha256(); "
            "files = sorted([os.path.join(r, f) for r, _, fs in os.walk('/workspace') if '.git' not in r for f in fs]); "
            "[h.update(open(f, 'rb').read()) for f in files]; "
            "print(h.hexdigest()[:16])\""
        )
        _, snap_out, _ = self.sandbox.exec_in_sandbox(snap_cmd, timeout_s=30)
        self.current_snapshot_id = snap_out.decode("utf-8").strip() or "init_snap"

        # Ensure permissions for runner user
        perm_cmd = "chown -R 10001:10001 /workspace && chmod -R 775 /workspace"
        self.sandbox.exec_in_sandbox(perm_cmd, user="0", timeout_s=30)

        return True, self.pinned_sha
