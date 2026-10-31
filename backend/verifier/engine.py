"""Independent verification engine: pure deterministic code, zero LLM calls."""

from __future__ import annotations

import logging
from typing import List, Optional

from backend.sandbox.api.schemas import (
    CheckResult,
    CheckVerdict,
    OverallVerdict,
    VerificationReport,
)
from backend.verifier.checks.cli import run_cli_check
from backend.verifier.checks.http import run_http_check
from backend.verifier.checks.integrity import check_test_integrity
from backend.verifier.checks.node import run_node_checks
from backend.verifier.checks.python import run_python_checks
from backend.verifier.plan import VerificationPlan
from backend.verifier.ports import CommandRunner, EventSink
from backend.verifier.verdict import aggregate_verdicts

logger = logging.getLogger(__name__)


class VerificationEngine:
    """Independent verification engine that decides whether a repository works."""

    def __init__(self, runner: CommandRunner, sink: Optional[EventSink] = None):
        self.runner = runner
        self.sink = sink

    def verify(
        self,
        run_id: str,
        entrypoint: Optional[str] = None,
        http_port: Optional[int] = None,
        http_path: str = "/",
        cli_command: Optional[str] = None,
        baseline_test_count: Optional[int] = None,
    ) -> VerificationReport:
        """
        Execute baseline checks and any additional specified checks.
        Produces deterministic VerificationReport without any model assistance.
        """
        if self.sink:
            self.sink.emit("verify.started", {"run_id": run_id})

        detected = VerificationPlan.detect_stack(self.runner)
        check_results: List[CheckResult] = []

        # 1. Stack-specific checks
        if detected["is_python"]:
            py_results = run_python_checks(
                runner=self.runner,
                entrypoint=entrypoint,
                run_tests=True,
            )
            for res in py_results:
                check_results.append(res)
                if self.sink:
                    self.sink.emit("check.finished", res.model_dump())

            # Test integrity
            integ_res = check_test_integrity(
                runner=self.runner,
                baseline_test_count=baseline_test_count,
                stack="python",
            )
            check_results.append(integ_res)
            if self.sink:
                self.sink.emit("check.finished", integ_res.model_dump())

        if detected["is_node"]:
            node_results = run_node_checks(runner=self.runner)
            for res in node_results:
                check_results.append(res)
                if self.sink:
                    self.sink.emit("check.finished", res.model_dump())

        # 2. HTTP probe check if web app or port specified
        if http_port or detected["is_web"]:
            target_port = http_port or 5000
            http_res = run_http_check(
                runner=self.runner,
                port=target_port,
                path=http_path,
                timeout_s=30,
            )
            check_results.append(http_res)
            if self.sink:
                self.sink.emit("check.finished", http_res.model_dump())

        # 3. CLI check if explicitly configured
        if cli_command:
            cli_res = run_cli_check(runner=self.runner, cli_cmd=cli_command)
            check_results.append(cli_res)
            if self.sink:
                self.sink.emit("check.finished", cli_res.model_dump())

        # 4. Deterministic verdict aggregation
        overall_status, completion_level = aggregate_verdicts(check_results)
        is_green = (overall_status == CheckVerdict.PASS and completion_level == OverallVerdict.COMPLETE)

        report = VerificationReport(
            run_id=run_id,
            overall_status=overall_status,
            completion_level=completion_level,
            checks=check_results,
            is_green=is_green,
        )

        if self.sink:
            self.sink.emit(
                "verify.finished",
                {
                    "overall_status": overall_status.value,
                    "completion_level": completion_level.value,
                    "is_green": is_green,
                },
            )

        return report
