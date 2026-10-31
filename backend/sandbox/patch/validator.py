"""Patch validation service with deterministic rejection rules."""

from __future__ import annotations

from typing import List, Optional, Tuple
from backend.sandbox.api.schemas import (
    PatchProposal,
    PatchRejectionCode,
    PatchResult,
    PatchStatus,
)
from backend.sandbox.patch.paths import check_scope, is_path_safe, normalize_relpath

MAX_FILES_PER_PATCH = 50
MAX_PATCH_BYTES = 1024 * 1024  # 1 MiB


class PatchValidator:
    """Validates patch proposals before any disk modification occurs."""

    @classmethod
    def validate(
        cls,
        proposal: PatchProposal,
        current_snapshot_id: str,
        run_is_active: bool = True,
    ) -> Optional[PatchResult]:
        """
        Validate proposal against all 8 validation rules.
        Returns None if valid, or a PatchResult with REJECTED status.
        """
        # Rule 1: Run state
        if not run_is_active:
            return PatchResult(
                patch_id=proposal.patch_id,
                run_id=proposal.run_id,
                status=PatchStatus.REJECTED,
                rejection_code=PatchRejectionCode.RUN_NOT_ACTIVE,
                rejection_reason="Run is not in ACTIVE state",
            )

        # Rule 2: Origin check (only orchestrator allowed)
        if proposal.origin != "orchestrator":
            return PatchResult(
                patch_id=proposal.patch_id,
                run_id=proposal.run_id,
                status=PatchStatus.REJECTED,
                rejection_code=PatchRejectionCode.ORIGIN_FORBIDDEN,
                rejection_reason=f"Origin '{proposal.origin}' is not authorized to submit patches",
            )

        # Rule 3: Format & size limits
        if not proposal.operations:
            return PatchResult(
                patch_id=proposal.patch_id,
                run_id=proposal.run_id,
                status=PatchStatus.REJECTED,
                rejection_code=PatchRejectionCode.MALFORMED,
                rejection_reason="Patch proposal contains no operations",
            )

        if len(proposal.operations) > MAX_FILES_PER_PATCH:
            return PatchResult(
                patch_id=proposal.patch_id,
                run_id=proposal.run_id,
                status=PatchStatus.REJECTED,
                rejection_code=PatchRejectionCode.MALFORMED,
                rejection_reason=f"Patch exceeds maximum file limit ({len(proposal.operations)} > {MAX_FILES_PER_PATCH})",
            )

        total_bytes = sum(
            len((op.content or "").encode("utf-8")) + len((op.diff_hunk or "").encode("utf-8"))
            for op in proposal.operations
        )
        if total_bytes > MAX_PATCH_BYTES:
            return PatchResult(
                patch_id=proposal.patch_id,
                run_id=proposal.run_id,
                status=PatchStatus.REJECTED,
                rejection_code=PatchRejectionCode.MALFORMED,
                rejection_reason=f"Patch payload exceeds 1 MiB limit ({total_bytes} bytes)",
            )

        # Rule 4: Path safety check
        touched_paths: List[str] = []
        for op in proposal.operations:
            safe, err = is_path_safe(op.path)
            if not safe:
                return PatchResult(
                    patch_id=proposal.patch_id,
                    run_id=proposal.run_id,
                    status=PatchStatus.REJECTED,
                    rejection_code=PatchRejectionCode.PATH_ESCAPE,
                    rejection_reason=f"Path validation failed for '{op.path}': {err}",
                )
            touched_paths.append(normalize_relpath(op.path))

        # Rule 5: Scope check
        if proposal.scope:
            in_scope, scope_err = check_scope(touched_paths, proposal.scope)
            if not in_scope:
                return PatchResult(
                    patch_id=proposal.patch_id,
                    run_id=proposal.run_id,
                    status=PatchStatus.REJECTED,
                    rejection_code=PatchRejectionCode.OUT_OF_SCOPE,
                    rejection_reason=scope_err,
                )

        # Rule 6: Snapshot freshness
        if proposal.base_snapshot_id != current_snapshot_id:
            return PatchResult(
                patch_id=proposal.patch_id,
                run_id=proposal.run_id,
                status=PatchStatus.REJECTED,
                rejection_code=PatchRejectionCode.STALE_SNAPSHOT,
                rejection_reason=(
                    f"Base snapshot '{proposal.base_snapshot_id}' is stale; "
                    f"current head snapshot is '{current_snapshot_id}'"
                ),
            )

        # Rule 7: Overlap check (no duplicate or conflicting operations in single patch)
        seen_paths = set()
        for p in touched_paths:
            if p in seen_paths:
                return PatchResult(
                    patch_id=proposal.patch_id,
                    run_id=proposal.run_id,
                    status=PatchStatus.REJECTED,
                    rejection_code=PatchRejectionCode.OVERLAP,
                    rejection_reason=f"Conflicting duplicate operations on path '{p}'",
                )
            seen_paths.add(p)

        # All validation checks passed
        return None
