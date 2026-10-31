"""Patch rollback service: restore any prior snapshot."""

from __future__ import annotations

import base64
import logging
from backend.sandbox.api.schemas import RollbackResult
from backend.sandbox.sandbox_manager.manager import SandboxManager

logger = logging.getLogger(__name__)


class PatchRollbackService:
    """Restores workspace files from shadow snapshot store."""

    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox

    def rollback(self, run_id: str, target_snapshot_id: str) -> RollbackResult:
        """
        Roll back workspace to the target_snapshot_id.
        Restores files from /workspace/.shadow_snapshots/<target_snapshot_id>.
        """
        python_script = f"""
import os, shutil, sys
target = '/workspace/.shadow_snapshots/{target_snapshot_id}'
if not os.path.exists(target):
    sys.exit(2)

for r, dirs, files in os.walk('/workspace', topdown=False):
    dirs[:] = [d for d in dirs if d not in ('.git', '.shadow_snapshots')]
    for f in files:
        p = os.path.join(r, f)
        if '.git' not in p and '.shadow_snapshots' not in p:
            try:
                os.remove(p)
            except Exception:
                pass

for r, dirs, files in os.walk(target):
    rel = os.path.relpath(r, target)
    dest_dir = os.path.join('/workspace', rel)
    os.makedirs(dest_dir, exist_ok=True)
    for f in files:
        shutil.copy2(os.path.join(r, f), os.path.join(dest_dir, f))
"""
        b64 = base64.b64encode(python_script.encode("utf-8")).decode("ascii")
        restore_cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}').decode('utf-8'))\""

        code, out, _ = self.sandbox.exec_in_sandbox(restore_cmd, timeout_s=30)
        if code != 0:
            logger.error("Rollback failed (exit %s): %s", code, out.decode("utf-8", errors="replace"))
            return RollbackResult(
                run_id=run_id,
                target_snapshot_id=target_snapshot_id,
                status="FAILED",
                restored_snapshot_id="FAILED",
                removed_files=[],
            )

        return RollbackResult(
            run_id=run_id,
            target_snapshot_id=target_snapshot_id,
            status="SUCCESS",
            restored_snapshot_id=target_snapshot_id,
            removed_files=[],
        )
