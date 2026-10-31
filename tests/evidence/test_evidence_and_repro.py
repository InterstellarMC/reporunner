"""Unit and integration tests for evidence generation and reproducibility checker."""

import json
import os
import pytest
from backend.evidence.dockerfile import generate_dockerfile
from backend.evidence.generator import EvidenceGenerator
from backend.sandbox.api.schemas import (
    CheckResult,
    CheckVerdict,
    CommandClass,
    CommandRequest,
    OverallVerdict,
    VerificationReport,
)
from backend.verifier.repro.checker import ReproducibilityChecker


def test_dockerfile_generation_from_ledger():
    ledger = [
        CommandRequest(
            run_id="run_1",
            command_id="cmd_1",
            command="pip install flask requests",
            command_class=CommandClass.INSTALL,
        ),
        CommandRequest(
            run_id="run_1",
            command_id="cmd_2",
            command="apt-get update && apt-get install -y libpq-dev",
            command_class=CommandClass.INSTALL,
        ),
    ]

    df = generate_dockerfile(
        base_image="python:3.11-slim",
        command_ledger=ledger,
        run_cmd="python3 server.py",
        port=5000,
    )

    assert "FROM python:3.11-slim" in df
    assert "RUN pip install flask requests" in df
    assert "RUN apt-get update && apt-get install -y libpq-dev" in df
    assert "EXPOSE 5000" in df
    assert 'CMD ["python3", "server.py"]' in df


def test_evidence_bundle_and_manifest_hashing(tmp_path):
    gen = EvidenceGenerator(run_id="test_evidence_run", base_dir=str(tmp_path))
    v_report = VerificationReport(
        run_id="test_evidence_run",
        overall_status=CheckVerdict.PASS,
        completion_level=OverallVerdict.COMPLETE,
        checks=[CheckResult(check_type="python_tests", verdict=CheckVerdict.PASS)],
        is_green=True,
    )

    bundle = gen.generate_bundle(
        verification_report=v_report,
        command_ledger=[],
        base_image="python:3.11-slim",
        run_cmd="python3 app.py",
    )

    manifest = bundle["manifest"]
    assert "Dockerfile" in manifest
    assert "RUN.md" in manifest
    assert "evidence.json" in manifest
    assert len(manifest["Dockerfile"]) == 64  # Valid SHA-256 length


def test_repro_checker_green_gate():
    v_report = VerificationReport(
        run_id="test_green",
        overall_status=CheckVerdict.PASS,
        completion_level=OverallVerdict.COMPLETE,
        checks=[],
        is_green=True,
    )

    # When all criteria met -> GREEN
    res = ReproducibilityChecker.evaluate_green(
        run_id="test_green",
        live_report=v_report,
        fresh_report=v_report,
        build_success=True,
        evidence_verified=True,
    )
    assert res.is_green is True
    assert len(res.reasons) == 0

    # When live verification was partial -> NOT_GREEN with reason
    v_partial = VerificationReport(
        run_id="test_partial",
        overall_status=CheckVerdict.FAIL,
        completion_level=OverallVerdict.PARTIAL,
        checks=[],
        is_green=False,
    )
    res_partial = ReproducibilityChecker.evaluate_green(
        run_id="test_partial",
        live_report=v_partial,
        build_success=True,
        evidence_verified=True,
    )
    assert res_partial.is_green is False
    assert any("not complete" in r for r in res_partial.reasons)
