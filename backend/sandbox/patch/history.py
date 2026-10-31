"""Patch history recorder for auditability and rollback tracking."""

from __future__ import annotations

from typing import Dict, List, Optional
from backend.sandbox.api.schemas import PatchProposal, PatchResult


class PatchHistory:
    """Maintains an ordered ledger of all patch proposals and outcomes for a run."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.entries: List[dict] = []
        self.applied_patches: Dict[str, PatchResult] = {}

    def record(self, proposal: PatchProposal, result: PatchResult) -> None:
        entry = {
            "patch_id": proposal.patch_id,
            "base_snapshot_id": proposal.base_snapshot_id,
            "new_snapshot_id": result.new_snapshot_id,
            "status": result.status.value,
            "changed_files": result.changed_files,
            "rejection_code": result.rejection_code.value if result.rejection_code else None,
        }
        self.entries.append(entry)
        if result.status.value == "APPLIED":
            self.applied_patches[proposal.patch_id] = result

    def get_last_applied(self) -> Optional[PatchResult]:
        for entry in reversed(self.entries):
            if entry["status"] == "APPLIED":
                return self.applied_patches.get(entry["patch_id"])
        return None
