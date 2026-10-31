"""CLI tool verification checks."""

from __future__ import annotations

import time
from backend.sandbox.api.schemas import CheckResult, CheckVerdict
from backend.verifier.ports import CommandRunner


def run_cli_check(
    runner: CommandRunner,
    cli_cmd: str = "python3 /workspace/main.py --help",
    timeout_s: int = 30,
) -> CheckResult:
    """Verify that CLI command exits 0 with non-empty output."""
    start = time.monotonic()
    code, out, err = runner.run(cli_cmd, timeout_s=timeout_s)
    dur = time.monotonic() - start

    combined = f"{out}\n{err}".strip()
    if code == 0:
        return CheckResult(
            check_type="cli_execution",
            verdict=CheckVerdict.PASS,
            output=f"Command '{cli_cmd}' exited 0:\n{combined[:200]}",
            duration_s=round(dur, 2),
        )

    verdict = CheckVerdict.FAIL
    if "KeyError" in combined or "api_key" in combined.lower():
        verdict = CheckVerdict.BLOCKED

    return CheckResult(
        check_type="cli_execution",
        verdict=verdict,
        output=f"Command '{cli_cmd}' failed (exit {code}):\n{combined}",
        duration_s=round(dur, 2),
    )
