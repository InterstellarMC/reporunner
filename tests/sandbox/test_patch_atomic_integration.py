"""Integration tests for atomic patch application and rollback in Docker sandbox."""

import pytest
from backend.sandbox.api.schemas import (
    CommandRequest,
    PatchOperation,
    PatchProposal,
    PatchStatus,
)
from backend.sandbox.facade import ExecutionService


@pytest.fixture(scope="module")
def execution_service():
    service = ExecutionService(run_id="test_patch_gate2", base_image="python:3.11-slim")
    service.provision_run()
    yield service
    service.close_run()


def test_apply_patch_and_verify(execution_service: ExecutionService):
    initial_snap = execution_service.current_snapshot_id

    # 1. Apply valid patch
    proposal = PatchProposal(
        patch_id="patch_001",
        run_id=execution_service.run_id,
        base_snapshot_id=initial_snap,
        operations=[
            PatchOperation(path="hello.py", content="print('hello from patch')\n"),
        ],
    )
    result = execution_service.apply_patch(proposal)
    assert result.status == PatchStatus.APPLIED
    assert result.new_snapshot_id != initial_snap
    new_snap = result.new_snapshot_id

    # 2. Verify file execution via command engine
    req = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_run_hello",
        command="python3 /workspace/hello.py",
    )
    res = execution_service.run_command(req)
    assert "hello from patch" in res.stdout

    # 3. Apply second patch
    proposal2 = PatchProposal(
        patch_id="patch_002",
        run_id=execution_service.run_id,
        base_snapshot_id=new_snap,
        operations=[
            PatchOperation(path="hello.py", content="print('updated version')\n"),
        ],
    )
    result2 = execution_service.apply_patch(proposal2)
    assert result2.status == PatchStatus.APPLIED

    # Check updated content
    req2 = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_run_hello2",
        command="python3 /workspace/hello.py",
    )
    res2 = execution_service.run_command(req2)
    assert "updated version" in res2.stdout

    # 4. Rollback to first patch snapshot
    rollback_res = execution_service.rollback(new_snap)
    assert rollback_res.status == "SUCCESS"

    # Verify content was restored to version 1
    req3 = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_run_hello3",
        command="python3 /workspace/hello.py",
    )
    res3 = execution_service.run_command(req3)
    assert "hello from patch" in res3.stdout
