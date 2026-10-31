"""Unit tests for deterministic verdict aggregation."""

from backend.sandbox.api.schemas import CheckResult, CheckVerdict, OverallVerdict
from backend.verifier.verdict import aggregate_verdicts


def test_aggregate_all_pass():
    checks = [
        CheckResult(check_type="python_compileall", verdict=CheckVerdict.PASS),
        CheckResult(check_type="python_pytest", verdict=CheckVerdict.PASS),
    ]
    overall, comp = aggregate_verdicts(checks)
    assert overall == CheckVerdict.PASS
    assert comp == OverallVerdict.COMPLETE


def test_aggregate_partial_pass():
    checks = [
        CheckResult(check_type="python_compileall", verdict=CheckVerdict.PASS),
        CheckResult(check_type="python_pytest", verdict=CheckVerdict.FAIL),
    ]
    overall, comp = aggregate_verdicts(checks)
    assert overall == CheckVerdict.FAIL
    assert comp == OverallVerdict.PARTIAL


def test_aggregate_blocked_precondition():
    checks = [
        CheckResult(check_type="python_compileall", verdict=CheckVerdict.PASS),
        CheckResult(check_type="http_probe", verdict=CheckVerdict.BLOCKED),
    ]
    overall, comp = aggregate_verdicts(checks)
    assert overall == CheckVerdict.BLOCKED
    assert comp == OverallVerdict.PARTIAL


def test_aggregate_not_applicable():
    checks = [
        CheckResult(check_type="python_compileall", verdict=CheckVerdict.PASS),
        CheckResult(check_type="node_test", verdict=CheckVerdict.NOT_APPLICABLE),
    ]
    overall, comp = aggregate_verdicts(checks)
    assert overall == CheckVerdict.PASS
    assert comp == OverallVerdict.COMPLETE
