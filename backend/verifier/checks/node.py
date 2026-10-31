"""Node.js independent verification checks."""

from __future__ import annotations

import time
from backend.sandbox.api.schemas import CheckResult, CheckVerdict
from backend.verifier.ports import CommandRunner


def run_node_checks(
    runner: CommandRunner,
    timeout_s: int = 180,
) -> list[CheckResult]:
    """Execute Node.js baseline verification checks."""
    results: list[CheckResult] = []

    # Check if package.json exists
    code, _, _ = runner.run("test -f /workspace/package.json", timeout_s=5)
    if code != 0:
        return [
            CheckResult(
                check_type="node_package_json",
                verdict=CheckVerdict.NOT_APPLICABLE,
                output="No package.json found",
            )
        ]

    # 1. npm test check
    start = time.monotonic()
    code, out, err = runner.run("npm test --if-present", timeout_s=timeout_s)
    dur = time.monotonic() - start
    combined = f"{out}\n{err}"

    if "no test specified" in combined.lower() or "missing script: test" in combined.lower():
        results.append(
            CheckResult(
                check_type="node_test",
                verdict=CheckVerdict.NOT_APPLICABLE,
                output="No test script defined in package.json",
                duration_s=round(dur, 2),
            )
        )
    elif code == 0:
        results.append(
            CheckResult(
                check_type="node_test",
                verdict=CheckVerdict.PASS,
                output="npm test passed",
                duration_s=round(dur, 2),
            )
        )
    else:
        results.append(
            CheckResult(
                check_type="node_test",
                verdict=CheckVerdict.FAIL,
                output=f"npm test failed:\n{combined}",
                duration_s=round(dur, 2),
            )
        )

    return results
