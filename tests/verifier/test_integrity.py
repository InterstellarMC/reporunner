"""Unit tests for test integrity assertions."""

from backend.sandbox.api.schemas import CheckVerdict
from backend.verifier.checks.integrity import check_test_integrity


class MockRunner:
    def __init__(self, output: str, code: int = 0):
        self.output = output
        self.code = code

    def run(self, cmd: str, timeout_s: int = 120, cwd: str = "/workspace"):
        return self.code, self.output, ""


def test_integrity_passes_when_test_count_meets_baseline():
    runner = MockRunner("collected 10 items\n")
    res = check_test_integrity(runner, baseline_test_count=8, stack="python")
    assert res.verdict == CheckVerdict.PASS


def test_integrity_fails_when_tests_were_deleted():
    # If baseline was 10, but patch deleted tests so only 6 remain
    runner = MockRunner("collected 6 items\n")
    res = check_test_integrity(runner, baseline_test_count=10, stack="python")
    assert res.verdict == CheckVerdict.FAIL
    assert "Test integrity violation" in res.output
