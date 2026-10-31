"""Deterministic verdict aggregation over observable verification checks."""

from __future__ import annotations

from typing import List, Tuple
from backend.sandbox.api.schemas import CheckResult, CheckVerdict, OverallVerdict


def aggregate_verdicts(checks: List[CheckResult]) -> Tuple[CheckVerdict, OverallVerdict]:
    """
    Aggregate check results deterministically according to spec:
    - COMPLETE: every required check is PASS (or NOT_APPLICABLE)
    - PARTIAL: at least one required check passed and at least one did not
    - NONE: no required check passed
    - Overall status is PASS only when COMPLETE. Otherwise worst non-pass present.
    """
    required_checks = [c for c in checks if c.required and c.verdict != CheckVerdict.NOT_APPLICABLE]

    if not required_checks:
        # All checks were not applicable
        return CheckVerdict.PASS, OverallVerdict.COMPLETE

    passed_count = sum(1 for c in required_checks if c.verdict == CheckVerdict.PASS)
    total_required = len(required_checks)

    # 1. Determine completion level
    if passed_count == total_required:
        completion_level = OverallVerdict.COMPLETE
    elif passed_count > 0:
        completion_level = OverallVerdict.PARTIAL
    else:
        completion_level = OverallVerdict.NONE

    # 2. Determine overall status
    if completion_level == OverallVerdict.COMPLETE:
        overall_status = CheckVerdict.PASS
    else:
        # Worst non-pass in order FAIL, TIMEOUT, BLOCKED
        verdicts = {c.verdict for c in required_checks}
        if CheckVerdict.FAIL in verdicts:
            overall_status = CheckVerdict.FAIL
        elif CheckVerdict.TIMEOUT in verdicts:
            overall_status = CheckVerdict.TIMEOUT
        elif CheckVerdict.BLOCKED in verdicts:
            overall_status = CheckVerdict.BLOCKED
        else:
            overall_status = CheckVerdict.FAIL

    return overall_status, completion_level
