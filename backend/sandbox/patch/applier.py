"""Atomic patch application service with rollback on failure."""

from __future__ import annotations

import base64
import logging
import threading
from typing import List, Optional
from backend.sandbox.api.schemas import (
    PatchProposal,
    PatchRejectionCode,
    PatchResult,
    PatchStatus,
)
from backend.sandbox.patch.history import PatchHistory
from backend.sandbox.patch.paths import normalize_relpath
from backend.sandbox.patch.rollback import PatchRollbackService
from backend.sandbox.patch.validator import PatchValidator
from backend.sandbox.sandbox_manager.manager import SandboxManager

logger = logging.getLogger(__name__)


class PatchApplier:
    """Applies validated patches atomically inside the sandbox."""

    def __init__(self, sandbox: SandboxManager, history: Optional[PatchHistory] = None):
        self.sandbox = sandbox
        self.history = history or PatchHistory(sandbox.run_id)
        self.rollback_service = PatchRollbackService(sandbox)
        self._lock = threading.Lock()

    def apply(
        self,
        proposal: PatchProposal,
        current_snapshot_id: str,
        is_active: bool = True,
    ) -> PatchResult:
        """Validate and apply a patch proposal atomically."""
        with self._lock:
            # 1. Validation phase
            rejection = PatchValidator.validate(
                proposal=proposal,
                current_snapshot_id=current_snapshot_id,
                run_is_active=is_active,
            )
            if rejection:
                self.history.record(proposal, rejection)
                return rejection

            # 2. Stage current snapshot in shadow store before making changes
            self._save_shadow_snapshot(current_snapshot_id)

            changed_files: List[str] = []

            # 3. Apply operations
            try:
                for op in proposal.operations:
                    rel_path = normalize_relpath(op.path)
                    dest_path = f"/workspace/{rel_path}"

                    if op.action == "delete":
                        del_cmd = f"rm -f '{dest_path}'"
                        code, out, _ = self.sandbox.exec_in_sandbox(del_cmd, timeout_s=10)
                        if code != 0:
                            raise RuntimeError(f"Failed to delete {rel_path}: {out.decode('utf-8')}")
                        changed_files.append(rel_path)

                    elif op.content is not None:
                        # Write full file content safely using base64 encoding to prevent shell escaping issues
                        b64_content = base64.b64encode(op.content.encode("utf-8")).decode("ascii")
                        write_cmd = (
                            f"python3 -c \""
                            f"import os, base64; "
                            f"target = '{dest_path}'; "
                            f"os.makedirs(os.path.dirname(target), exist_ok=True); "
                            f"open(target, 'wb').write(base64.b64decode('{b64_content}')); "
                            f"\""
                        )
                        code, out, _ = self.sandbox.exec_in_sandbox(write_cmd, timeout_s=15)
                        if code != 0:
                            raise RuntimeError(f"Failed to write file {rel_path}: {out.decode('utf-8')}")
                        changed_files.append(rel_path)

                    elif op.diff_hunk:
                        # Apply unified diff using patch utility or python
                        b64_diff = base64.b64encode(op.diff_hunk.encode("utf-8")).decode("ascii")
                        patch_cmd = (
                            f"python3 -c \""
                            f"import subprocess, base64; "
                            f"diff = base64.b64decode('{b64_diff}'); "
                            f"p = subprocess.run(['patch', '-p1', '-u'], input=diff, cwd='/workspace', capture_output=True); "
                            f"if p.returncode != 0: raise RuntimeError(p.stderr.decode()); "
                            f"\""
                        )
                        code, out, _ = self.sandbox.exec_in_sandbox(patch_cmd, timeout_s=20)
                        if code != 0:
                            raise RuntimeError(f"Patch hunk failed to apply on {rel_path}: {out.decode('utf-8')}")
                        changed_files.append(rel_path)

                # Compute new snapshot ID
                new_snap_id = self._compute_and_save_snapshot()

                result = PatchResult(
                    patch_id=proposal.patch_id,
                    run_id=proposal.run_id,
                    status=PatchStatus.APPLIED,
                    new_snapshot_id=new_snap_id,
                    changed_files=changed_files,
                    diff_summary=f"Applied {len(changed_files)} file modification(s)",
                )
                self.history.record(proposal, result)
                return result

            except Exception as e:
                logger.error("Patch application failed, rolling back to %s: %s", current_snapshot_id, e)
                # Automatically rollback to base_snapshot_id
                self.rollback_service.rollback(proposal.run_id, current_snapshot_id)

                fail_res = PatchResult(
                    patch_id=proposal.patch_id,
                    run_id=proposal.run_id,
                    status=PatchStatus.FAILED,
                    rejection_code=PatchRejectionCode.DOES_NOT_APPLY,
                    rejection_reason=f"Apply failed: {e}",
                    changed_files=[],
                )
                self.history.record(proposal, fail_res)
                return fail_res

    def _save_shadow_snapshot(self, snap_id: str) -> None:
        """Archive current workspace files under /workspace/.shadow_snapshots/<snap_id>."""
        python_script = f"""
import os, shutil
dest = '/workspace/.shadow_snapshots/{snap_id}'
if not os.path.exists(dest):
    os.makedirs(dest, exist_ok=True)
    for r, dirs, files in os.walk('/workspace'):
        dirs[:] = [d for d in dirs if d not in ('.git', '.shadow_snapshots')]
        for f in files:
            s = os.path.join(r, f)
            rel = os.path.relpath(s, '/workspace')
            d = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
"""
        b64 = base64.b64encode(python_script.encode("utf-8")).decode("ascii")
        cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}').decode('utf-8'))\""
        self.sandbox.exec_in_sandbox(cmd, timeout_s=30)

    def _compute_and_save_snapshot(self) -> str:
        """Compute content hash and archive new snapshot."""
        python_script = """
import os, hashlib
h = hashlib.sha256()
files = sorted([os.path.join(r, f) for r, dirs, fs in os.walk('/workspace') if '.git' not in r and '.shadow_snapshots' not in r for f in fs])
for f in files:
    try:
        h.update(open(f, 'rb').read())
    except Exception:
        pass
print(h.hexdigest()[:16])
"""
        b64 = base64.b64encode(python_script.encode("utf-8")).decode("ascii")
        cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}').decode('utf-8'))\""
        _, out, _ = self.sandbox.exec_in_sandbox(cmd, timeout_s=20)
        new_snap_id = out.decode("utf-8").strip() or "snap_next"
        self._save_shadow_snapshot(new_snap_id)
        return new_snap_id
