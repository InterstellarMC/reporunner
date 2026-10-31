"""Schemas for benchmark manifest entries and execution results."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BenchmarkEntry(BaseModel):
    """Manifest specification for a single benchmark repository."""
    id: str
    name: str
    stack: str = "python"
    framework: Optional[str] = None
    fault: Optional[str] = None
    verification_type: str = "pytest"
    http_port: Optional[int] = None
    cli_command: Optional[str] = None
    expected_outcome: str  # "GREEN" or "BLOCKED"
    repo_url: str
    commit_sha: str
    blocker_type: Optional[str] = None
    blocker_name: Optional[str] = None


class BenchmarkResult(BaseModel):
    """Evidence-backed result record for a benchmark run."""
    entry_id: str
    repo_url: str
    expected_commit_sha: str
    actual_commit_sha: Optional[str] = None
    expected_outcome: str
    actual_outcome: str  # "GREEN", "BLOCKED", "FAILED", "CLONE_FAILED"
    match: bool = False
    execution_steps: int = 0
    wall_clock_duration: float = 0.0
    wall_clock_seconds: float = 0.0
    command_ledger: List[Dict[str, Any]] = Field(default_factory=list)
    patch_history: List[Dict[str, Any]] = Field(default_factory=list)
    verification_report: Optional[Dict[str, Any]] = None
    fresh_build_result: Optional[Dict[str, Any]] = None
    fresh_container_verification_result: Optional[Dict[str, Any]] = None
    fresh_verification_result: Optional[Dict[str, Any]] = None
    reproducibility_verdict: Optional[bool] = None
    evidence_directory: Optional[str] = None
    failure_reason: Optional[str] = None
    blocker_reason: Optional[str] = None
    simulated: bool = False
    details: Optional[str] = None
