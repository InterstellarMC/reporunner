"""Integration tests for live Docker sandbox execution."""

import pytest
from backend.sandbox.api.schemas import CommandClass, CommandRequest, ExecutionStatus
from backend.sandbox.facade import ExecutionService


@pytest.fixture(scope="module")
def execution_service():
    service = ExecutionService(run_id="test_integ_gate1", base_image="python:3.11-slim")
    service.provision_run()
    yield service
    service.close_run()


def test_sandbox_runs_as_non_root(execution_service: ExecutionService):
    req = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_check_user",
        command="id -u && whoami",
        command_class=CommandClass.ADMIN,
    )
    res = execution_service.run_command(req)
    assert res.status == ExecutionStatus.SUCCESS
    assert "10001" in res.stdout
    assert "runner" in res.stdout


def test_sandbox_executes_python(execution_service: ExecutionService):
    req = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_check_python",
        command="python3 -c 'import sys; print(f\"Python {sys.version_info.major}.{sys.version_info.minor}\")'",
        command_class=CommandClass.RUN,
    )
    res = execution_service.run_command(req)
    assert res.status == ExecutionStatus.SUCCESS
    assert "Python 3.11" in res.stdout


def test_command_idempotency(execution_service: ExecutionService):
    req1 = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_idempotent_test",
        command="echo first_run",
        command_class=CommandClass.RUN,
    )
    res1 = execution_service.run_command(req1)
    assert "first_run" in res1.stdout

    # Repeat with same command_id but different command string - must return cached res1
    req2 = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_idempotent_test",
        command="echo second_run",
        command_class=CommandClass.RUN,
    )
    res2 = execution_service.run_command(req2)
    assert res2.stdout == res1.stdout
    assert "first_run" in res2.stdout


def test_policy_violation_blocked_in_engine(execution_service: ExecutionService):
    req = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_policy_blocked",
        command="sudo rm -rf /workspace",
        command_class=CommandClass.RUN,
    )
    res = execution_service.run_command(req)
    assert res.status == ExecutionStatus.BLOCKED_POLICY
    assert "Security policy violation" in res.stderr


def test_secret_redaction_in_sandbox(execution_service: ExecutionService):
    secret_val = "SECRET_TOKEN_XYZ_999"
    req = CommandRequest(
        run_id=execution_service.run_id,
        command_id="cmd_secret_test",
        command=f"echo 'API token is {secret_val}'",
        command_class=CommandClass.RUN,
        env={"MY_KEY": secret_val},
        secret_keys=["MY_KEY"],
    )
    res = execution_service.run_command(req)
    assert res.status == ExecutionStatus.SUCCESS
    assert secret_val not in res.stdout
    assert "[REDACTED]" in res.stdout
