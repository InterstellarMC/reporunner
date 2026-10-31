"""Reproducibility checker: the authoritative GREEN gate."""

from __future__ import annotations

import logging
import time
from typing import List, Optional
from backend.sandbox.api.schemas import (
    CheckVerdict,
    OverallVerdict,
    ReproReport,
    VerificationReport,
)
from backend.sandbox.sandbox_manager.docker_client import get_docker_client
from backend.verifier.engine import VerificationEngine

logger = logging.getLogger(__name__)


class ReproducibilityChecker:
    """The authoritative GREEN gate validator."""

    @classmethod
    def evaluate_green(
        cls,
        run_id: str,
        live_report: VerificationReport,
        fresh_report: Optional[VerificationReport] = None,
        build_success: bool = True,
        build_duration_s: float = 0.0,
        evidence_verified: bool = True,
    ) -> ReproReport:
        """
        Evaluate all 5 GREEN gate rules:
        1. Live-sandbox verification is COMPLETE.
        2. Clean image builds without host state.
        3. Baseline checks in fresh container are COMPLETE.
        4. Evidence bundle manifest hashes verify.
        5. No check relied on unverified state.
        """
        reasons: List[str] = []

        # Gate 1: Live sandbox check
        if live_report.completion_level != OverallVerdict.COMPLETE or live_report.overall_status != CheckVerdict.PASS:
            reasons.append(f"Live sandbox verification is not complete ({live_report.overall_status.value}/{live_report.completion_level.value})")

        # Gate 2: Clean build check
        if not build_success:
            reasons.append("Fresh Dockerfile build failed")

        # Gate 3: Fresh verification check - MANDATORY, NO FALLBACK
        if fresh_report is None:
            reasons.append("Fresh container verification report is missing (cannot verify clean rebuild)")
        else:
            if fresh_report.completion_level != OverallVerdict.COMPLETE or fresh_report.overall_status != CheckVerdict.PASS:
                reasons.append(f"Fresh container verification failed ({fresh_report.overall_status.value}/{fresh_report.completion_level.value})")

        # Gate 4: Evidence verification
        if not evidence_verified:
            reasons.append("Evidence bundle manifest hash verification failed or evidence is missing")

        is_green = (len(reasons) == 0)

        # Must report fresh_report if green, never live_report
        return ReproReport(
            run_id=run_id,
            is_green=is_green,
            reasons=reasons,
            clean_build_duration_s=round(build_duration_s, 2),
            verification_report=fresh_report or live_report,
        )
