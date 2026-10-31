"""Contract models and schemas for execution and verification."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CommandClass(str, Enum):
    FETCH = "fetch"
    BUILD = "build"
    INSTALL = "install"
    RUN = "run"
    TEST = "test"
    PROBE = "probe"
    ADMIN = "admin"


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    OOM_KILLED = "OOM_KILLED"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    SANDBOX_ERROR = "SANDBOX_ERROR"


class ErrorCategory(str, Enum):
    NONE = "NONE"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    DEPENDENCY_CONFLICT = "DEPENDENCY_CONFLICT"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    IMPORT_ERROR = "IMPORT_ERROR"
    ENV_VAR_MISSING = "ENV_VAR_MISSING"
    CONFIG_MISSING = "CONFIG_MISSING"
    PORT_IN_USE = "PORT_IN_USE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    BUILD_FAILURE = "BUILD_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    RUNTIME_EXCEPTION = "RUNTIME_EXCEPTION"
    TIMEOUT = "TIMEOUT"
    OOM = "OOM"
    DISK_FULL = "DISK_FULL"
    UNKNOWN = "UNKNOWN"


class CommandRequest(BaseModel):
    run_id: str
    command_id: str
    command: str
    cwd: str = "/workspace"
    timeout_s: int = 120
    command_class: CommandClass = CommandClass.RUN
    env: Dict[str, str] = Field(default_factory=dict)
    secret_keys: List[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    run_id: str
    command_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    status: ExecutionStatus
    error_category: ErrorCategory
    fingerprint: Optional[str] = None
    truncated: bool = False
    network_mode: Optional[str] = None
    snapshot_id: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class PatchOperation(BaseModel):
    path: str
    action: str = "modify"  # "modify", "add", "delete"
    diff_hunk: Optional[str] = None
    content: Optional[str] = None


class PatchProposal(BaseModel):
    patch_id: str
    run_id: str
    base_snapshot_id: str
    scope: List[str] = Field(default_factory=list)
    operations: List[PatchOperation] = Field(default_factory=list)
    origin: str = "orchestrator"
    decision_id: Optional[str] = None


class PatchStatus(str, Enum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PatchRejectionCode(str, Enum):
    RUN_NOT_ACTIVE = "RUN_NOT_ACTIVE"
    ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
    MALFORMED = "MALFORMED"
    PATH_ESCAPE = "PATH_ESCAPE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    OVERLAP = "OVERLAP"
    DOES_NOT_APPLY = "DOES_NOT_APPLY"


class PatchResult(BaseModel):
    patch_id: str
    run_id: str
    status: PatchStatus
    new_snapshot_id: Optional[str] = None
    rejection_code: Optional[PatchRejectionCode] = None
    rejection_reason: Optional[str] = None
    changed_files: List[str] = Field(default_factory=list)
    diff_summary: Optional[str] = None


class RollbackResult(BaseModel):
    run_id: str
    target_snapshot_id: str
    status: str
    restored_snapshot_id: str
    removed_files: List[str] = Field(default_factory=list)


class CheckVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CheckResult(BaseModel):
    check_type: str
    verdict: CheckVerdict
    required: bool = True
    output: str = ""
    blocker_reason: Optional[str] = None
    duration_s: float = 0.0


class OverallVerdict(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class VerificationReport(BaseModel):
    run_id: str
    overall_status: CheckVerdict
    completion_level: OverallVerdict
    checks: List[CheckResult] = Field(default_factory=list)
    is_green: bool = False
    evidence_path: Optional[str] = None


class ReproReport(BaseModel):
    run_id: str
    is_green: bool
    reasons: List[str] = Field(default_factory=list)
    clean_build_duration_s: float = 0.0
    verification_report: Optional[VerificationReport] = None


class RunLimits(BaseModel):
    max_steps: int = 25
    max_duration_seconds: int = 600
    command_timeout_seconds: int = 120
    memory_limit: str = "4g"
    cpu_limit: float = 2.0
    pids_limit: int = 512
