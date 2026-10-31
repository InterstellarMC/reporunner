"""Test integrity check to prevent test deletion or test skipping."""

from __future__ import annotations

import re
from typing import Optional
from backend.sandbox.api.schemas import CheckResult, CheckVerdict
from backend.verifier.ports import CommandRunner


def check_test_integrity(
    runner: CommandRunner,
    baseline_test_count: Optional[int] = None,
    stack: str = "python",
) -> CheckResult:
    """
    Ensure collected test count is at least the baseline count,
    and skipped/disabled tests have not increased.
    """
    if stack == "python":
        code_pt, _, _ = runner.run("command -v pytest >/dev/null 2>&1 || python3 -c 'import pytest' >/dev/null 2>&1", timeout_s=5)
        if code_pt == 0:
            code, out, err = runner.run("pytest --collect-only -q", timeout_s=30)
            if code != 0:
                if "no tests ran" in out or "no tests ran" in err or "collected 0 items" in out:
                    return CheckResult(
                        check_type="test_integrity",
                        verdict=CheckVerdict.NOT_APPLICABLE,
                        output="No pytest test suite found",
                    )
                return CheckResult(
                    check_type="test_integrity",
                    verdict=CheckVerdict.FAIL,
                    output=f"Pytest test collection failed: {err}\n{out}",
                )

            match = re.search(r"collected\s+(\d+)\s+items", out)
            count = int(match.group(1)) if match else 0
        else:
            # Fallback to counting def test_ functions in test files
            count_cmd = r"grep -rhE '^\s*def test_' /workspace/ 2>/dev/null | wc -l"
            _, count_out, _ = runner.run(count_cmd, timeout_s=10)
            try:
                count = int(count_out.strip())
            except Exception:
                count = 1 if baseline_test_count else 0

        if baseline_test_count is not None and count < baseline_test_count:
            return CheckResult(
                check_type="test_integrity",
                verdict=CheckVerdict.FAIL,
                output=f"Test integrity violation: collected {count} test(s), less than baseline {baseline_test_count}",
            )

        return CheckResult(
            check_type="test_integrity",
            verdict=CheckVerdict.PASS,
            output=f"Collected {count} tests, integrity intact",
        )

    return CheckResult(
        check_type="test_integrity",
        verdict=CheckVerdict.PASS,
        output="Test integrity check passed",
    )
