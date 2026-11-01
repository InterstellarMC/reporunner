"""Version 1 wire contracts; schemas are generated from these models."""
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Specialist(str, Enum):
    repository = "repository"
    environment = "environment"
    dependency = "dependency"
    configuration = "configuration"
    build = "build"
    test = "test"


def safe_path(value: str) -> str:
    if not value or value.startswith(("/", "\\")) or "\\" in value or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("path must be relative and normalized")
    if any(p in (".git", ".env", ".ssh") or p.startswith(".env.") or p.endswith((".pem", ".key")) for p in value.split("/")):
        raise ValueError("sensitive or internal file is outside model scope")
    return value


class RepositoryContext(Wire):
    schema_version: Literal["1.0"] = "1.0"
    repository_url: str
    snapshot: str = Field(min_length=1)
    files: dict[str, str] = Field(max_length=100)
    stack: Literal["python", "node", "go", "rust", "java", "dotnet", "unknown"] = "unknown"

    @field_validator("files")
    @classmethod
    def bounded_files(cls, files):
        for path in files:
            safe_path(path)
        if sum(len(v.encode()) for v in files.values()) > 200_000:
            raise ValueError("context exceeds 200 KB")
        return files


class WorkerTask(Wire):
    task_id: str
    specialist: Specialist
    objective: str = Field(min_length=1, max_length=3000)
    scope: list[str] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=10)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value):
        return [safe_path(v) for v in value]


class PatchProposal(Wire):
    task_id: str
    snapshot: str
    files: list[str] = Field(min_length=1, max_length=20)
    unified_diff: str = Field(min_length=1, max_length=50_000)
    reason: str

    @field_validator("files")
    @classmethod
    def validate_files(cls, value):
        return [safe_path(v) for v in value]


class WorkerResult(Wire):
    task_id: str
    status: Literal["completed", "blocked"]
    findings: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    patches: list[PatchProposal] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class Command(Wire):
    argv: list[str] = Field(min_length=1, max_length=50)
    cwd: str = "."
    timeout_seconds: float = Field(default=60, gt=0, le=600)

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, v):
        return v if v == "." else safe_path(v)


class Decomposition(Wire):
    tasks: list[WorkerTask] = Field(default_factory=list, max_length=8)
    human_reason: str | None = None


class Decision(Wire):
    accepted_task_ids: list[str] = Field(default_factory=list)
    commands: list[Command] = Field(default_factory=list, max_length=8)
    rationale: str = ""
    human_reason: str | None = None


class ActionRequest(Wire):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    action_id: str
    snapshot: str
    patches: list[PatchProposal]
    commands: list[Command] = Field(min_length=1, max_length=1)
    resource_refs: list[str] = Field(default_factory=list)
    remaining_seconds: float = Field(gt=0, le=600)


class ExecutionResult(Wire):
    run_id: str
    action_id: str
    exit_code: int
    stdout: str = Field(max_length=50_000)
    stderr: str = Field(max_length=50_000)
    timed_out: bool = False
    context: RepositoryContext
    blocker: Literal["secret", "gpu", "private_package"] | None = None


class Check(Wire):
    name: str
    kind: Literal["test", "http", "rebuild"]
    passed: bool
    evidence_ref: str = Field(min_length=1)


class EvidenceManifest(Wire):
    run_id: str
    snapshot: str
    artifacts: dict[str, str]


class VerificationResult(Wire):
    run_id: str
    snapshot: str
    checks: list[Check]
    evidence: EvidenceManifest
    diagnostic: str = Field(default="", max_length=12000)

    def passes(self) -> bool:
        kinds = {c.kind for c in self.checks if c.passed}
        return (bool(self.checks) and all(c.passed for c in self.checks)
                and "rebuild" in kinds and bool(kinds & {"test", "http"})
                and {"Dockerfile", "RUN.md"} <= self.evidence.artifacts.keys())


class HumanApproval(Wire):
    approval_id: str
    approved: bool
    resource_refs: list[str] = Field(default_factory=list, max_length=20)


class RunEvent(Wire):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    sequence: int
    timestamp: str
    type: str
    data: dict


class RunView(Wire):
    run_id: str
    status: Literal["running", "awaiting_approval", "completed", "stopped", "failed"] = "running"
    grade: Literal["GREEN", "AMBER", "RED"] = "RED"
    steps: int = 0
    rounds: int = 0
    reason: str = ""
    pending_approval: str | None = None
    approval_kind: Literal["resource", "final", "review"] | None = None
    verification: VerificationResult | None = None
