"""Integration tests for verification engine inside live Docker sandbox."""

import pytest
from backend.sandbox.api.schemas import (
    CheckVerdict,
    OverallVerdict,
    PatchOperation,
    PatchProposal,
)
from backend.sandbox.facade import ExecutionService


@pytest.fixture(scope="module")
def execution_service():
    service = ExecutionService(run_id="test_verify_gate4", base_image="python:3.11-slim")
    service.provision_run()
    yield service
    service.close_run()


def test_independent_verification_e2e(execution_service: ExecutionService):
    # 1. Apply patch creating a valid python module and passing pytest test
    proposal = PatchProposal(
        patch_id="patch_setup_test_app",
        run_id=execution_service.run_id,
        base_snapshot_id=execution_service.current_snapshot_id,
        operations=[
            PatchOperation(
                path="app.py",
                content="def add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n    print('app running')\n",
            ),
            PatchOperation(
                path="test_app.py",
                content="from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            ),
        ],
    )
    patch_res = execution_service.apply_patch(proposal)
    assert patch_res.status.value == "APPLIED"

    # 2. Run verification engine
    report = execution_service.verify(
        entrypoint="app.py",
        cli_command="python3 /workspace/app.py",
        baseline_test_count=1,
    )

    print("ALL CHECKS:")
    for c in report.checks:
        print(f"  {c.check_type}: verdict={c.verdict.value}, output={c.output}")

    # 3. Assert report results
    assert report.overall_status == CheckVerdict.PASS
    assert report.completion_level == OverallVerdict.COMPLETE
    assert report.is_green is True
