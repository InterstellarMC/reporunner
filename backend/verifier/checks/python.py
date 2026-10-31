"""Python independent verification checks."""

from __future__ import annotations

import time
from typing import Optional
from backend.sandbox.api.schemas import CheckResult, CheckVerdict
from backend.verifier.ports import CommandRunner


def run_python_checks(
    runner: CommandRunner,
    entrypoint: Optional[str] = None,
    run_tests: bool = True,
    timeout_s: int = 180,
) -> list[CheckResult]:
    """Execute Python verification assertions."""
    results: list[CheckResult] = []

    # 1. Compile check: compileall
    start = time.monotonic()
    code, out, err = runner.run("python3 -m compileall -q /workspace", timeout_s=30)
    dur = time.monotonic() - start
    if code != 0:
        results.append(
            CheckResult(
                check_type="python_compileall",
                verdict=CheckVerdict.FAIL,
                output=f"compileall failed (exit {code}):\n{err}\n{out}",
                duration_s=round(dur, 2),
            )
        )
    else:
        results.append(
            CheckResult(
                check_type="python_compileall",
                verdict=CheckVerdict.PASS,
                output="compileall succeeded cleanly",
                duration_s=round(dur, 2),
            )
        )

    # 2. Entrypoint import check
    if entrypoint:
        start = time.monotonic()
        mod_name = entrypoint.replace(".py", "").replace("/", ".").strip(".")
        import_cmd = f"python3 -c \"import sys; sys.path.insert(0, '/workspace'); import {mod_name}\""
        code, out, err = runner.run(import_cmd, timeout_s=30)
        dur = time.monotonic() - start
        if code != 0:
            verdict = CheckVerdict.BLOCKED if "KeyError" in err or "API" in err else CheckVerdict.FAIL
            results.append(
                CheckResult(
                    check_type="python_import_entrypoint",
                    verdict=verdict,
                    output=f"Importing entrypoint {entrypoint} failed:\n{err}\n{out}",
                    duration_s=round(dur, 2),
                )
            )
        else:
            results.append(
                CheckResult(
                    check_type="python_import_entrypoint",
                    verdict=CheckVerdict.PASS,
                    output=f"Imported entrypoint {entrypoint} successfully",
                    duration_s=round(dur, 2),
                )
            )

    # 3. Test suite check
    if run_tests:
        start = time.monotonic()
        # Prefer pytest if available in the sandbox, fallback to stdlib unittest
        code_pt, _, _ = runner.run("command -v pytest >/dev/null 2>&1 || python3 -c 'import pytest' >/dev/null 2>&1", timeout_s=5)
        if code_pt == 0:
            test_cmd = "pytest -x -q"
        else:
            test_cmd = "python3 -m unittest discover -s /workspace -p 'test*.py'"

        code, out, err = runner.run(test_cmd, timeout_s=timeout_s)
        dur = time.monotonic() - start
        if code == 0:
            results.append(
                CheckResult(
                    check_type="python_tests",
                    verdict=CheckVerdict.PASS,
                    output=out or err or "Tests passed",
                    duration_s=round(dur, 2),
                )
            )
        elif code == 5:
            # Pytest code 5 means no tests were collected
            results.append(
                CheckResult(
                    check_type="python_tests",
                    verdict=CheckVerdict.NOT_APPLICABLE,
                    output="No tests discovered in repository",
                    duration_s=round(dur, 2),
                )
            )
        else:
            results.append(
                CheckResult(
                    check_type="python_tests",
                    verdict=CheckVerdict.FAIL,
                    output=f"Tests failed (exit {code}):\n{err}\n{out}",
                    duration_s=round(dur, 2),
                )
            )

    return results
