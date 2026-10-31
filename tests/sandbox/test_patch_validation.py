"""Unit tests for patch validation rules and rejection codes."""

import pytest
from backend.sandbox.api.schemas import (
    PatchOperation,
    PatchProposal,
    PatchRejectionCode,
    PatchStatus,
)
from backend.sandbox.patch.validator import PatchValidator


def test_reject_inactive_run():
    proposal = PatchProposal(
        patch_id="p1",
        run_id="run_1",
        base_snapshot_id="snap_1",
        operations=[PatchOperation(path="main.py", content="print('hello')")],
    )
    result = PatchValidator.validate(proposal, current_snapshot_id="snap_1", run_is_active=False)
    assert result is not None
    assert result.status == PatchStatus.REJECTED
    assert result.rejection_code == PatchRejectionCode.RUN_NOT_ACTIVE


def test_reject_forbidden_origin():
    proposal = PatchProposal(
        patch_id="p1",
        run_id="run_1",
        base_snapshot_id="snap_1",
        origin="specialist_worker",
        operations=[PatchOperation(path="main.py", content="print('hello')")],
    )
    result = PatchValidator.validate(proposal, current_snapshot_id="snap_1", run_is_active=True)
    assert result is not None
    assert result.rejection_code == PatchRejectionCode.ORIGIN_FORBIDDEN


def test_reject_malformed_empty_ops():
    proposal = PatchProposal(
        patch_id="p1",
        run_id="run_1",
        base_snapshot_id="snap_1",
        operations=[],
    )
    result = PatchValidator.validate(proposal, current_snapshot_id="snap_1", run_is_active=True)
    assert result is not None
    assert result.rejection_code == PatchRejectionCode.MALFORMED


def test_reject_path_escape():
    for bad_path in ["../secret.txt", "/etc/passwd", ".git/config", ".shadow_snapshots/snap1"]:
        proposal = PatchProposal(
            patch_id="p1",
            run_id="run_1",
            base_snapshot_id="snap_1",
            operations=[PatchOperation(path=bad_path, content="hacked")],
        )
        result = PatchValidator.validate(proposal, current_snapshot_id="snap_1", run_is_active=True)
        assert result is not None
        assert result.rejection_code == PatchRejectionCode.PATH_ESCAPE, f"Failed on {bad_path}"


def test_reject_out_of_scope():
    proposal = PatchProposal(
        patch_id="p1",
        run_id="run_1",
        base_snapshot_id="snap_1",
        scope=["src/", "README.md"],
        operations=[PatchOperation(path="tests/test_app.py", content="bad")],
    )
    result = PatchValidator.validate(proposal, current_snapshot_id="snap_1", run_is_active=True)
    assert result is not None
    assert result.rejection_code == PatchRejectionCode.OUT_OF_SCOPE


def test_scenario_s2_stale_snapshot():
    proposal = PatchProposal(
        patch_id="p1",
        run_id="run_1",
        base_snapshot_id="old_snap_000",
        operations=[PatchOperation(path="main.py", content="print(1)")],
    )
    result = PatchValidator.validate(proposal, current_snapshot_id="current_head_111", run_is_active=True)
    assert result is not None
    assert result.rejection_code == PatchRejectionCode.STALE_SNAPSHOT
    assert "current_head_111" in (result.rejection_reason or "")


def test_scenario_s3_overlap_duplicate_paths():
    proposal = PatchProposal(
        patch_id="p1",
        run_id="run_1",
        base_snapshot_id="snap_1",
        operations=[
            PatchOperation(path="main.py", content="print(1)"),
            PatchOperation(path="main.py", content="print(2)"),
        ],
    )
    result = PatchValidator.validate(proposal, current_snapshot_id="snap_1", run_is_active=True)
    assert result is not None
    assert result.rejection_code == PatchRejectionCode.OVERLAP
