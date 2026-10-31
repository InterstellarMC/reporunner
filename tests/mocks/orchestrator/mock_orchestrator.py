"""Mock orchestrator that runs scripted scenarios S1-S7 to generate golden event files."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List
from backend.sandbox.api.schemas import (
    CommandRequest,
    PatchOperation,
    PatchProposal,
    RunLimits,
)
from backend.sandbox.events.emitter import EventEmitter
from backend.sandbox.events.store import EventStore


def generate_scenario_s1_happy_path(run_id: str = "golden_s1_happy") -> List[Dict[str, Any]]:
    """S1: Happy path - provision, initial failure, patch fix, success, verify, green."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "repo": "https://github.com/example/flask-app", "base_image": "python:3.11-slim"})
    emitter.emit("command.started", {"command_id": "c1", "command": "python3 app.py", "cwd": "/workspace"})
    emitter.emit("command.finished", {"command_id": "c1", "exit_code": 1, "status": "FAILED", "error_category": "IMPORT_ERROR", "fingerprint": "IMPORT_ERROR:8f3c1a"})
    emitter.emit("patch.received", {"patch_id": "p1", "action": "apply"})
    emitter.emit("patch.applied", {"patch_id": "p1", "new_snapshot_id": "snap_fix_01", "changed_files": ["requirements.txt"]})
    emitter.emit("command.started", {"command_id": "c2", "command": "pip install -r requirements.txt", "cwd": "/workspace"})
    emitter.emit("command.finished", {"command_id": "c2", "exit_code": 0, "status": "SUCCESS", "error_category": "NONE"})
    emitter.emit("process.started", {"process_id": "proc_web", "port": 5000})
    emitter.emit("verify.started", {"run_id": run_id, "checks": ["http", "python"]})
    emitter.emit("check.finished", {"check_type": "http", "verdict": "PASS", "duration_s": 0.4})
    emitter.emit("verify.finished", {"overall_status": "PASS", "completion_level": "COMPLETE", "is_green": True})
    emitter.emit("repro.finished", {"is_green": True, "reasons": []})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_scenario_s2_stale_snapshot(run_id: str = "golden_s2_stale") -> List[Dict[str, Any]]:
    """S2: Stale snapshot rejection."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "base_image": "python:3.11-slim"})
    emitter.emit("patch.received", {"patch_id": "p1", "action": "apply"})
    emitter.emit("patch.applied", {"patch_id": "p1", "new_snapshot_id": "head_snap_999", "changed_files": ["app.py"]})
    emitter.emit("patch.received", {"patch_id": "p2", "action": "apply"})
    emitter.emit("patch.rejected", {"patch_id": "p2", "rejection_code": "STALE_SNAPSHOT", "reason": "Base snapshot old_snap_000 is stale; current head is head_snap_999"})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_scenario_s3_overlap(run_id: str = "golden_s3_overlap") -> List[Dict[str, Any]]:
    """S3: Conflicting operations on the same path."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "base_image": "python:3.11-slim"})
    emitter.emit("patch.received", {"patch_id": "p1", "action": "apply"})
    emitter.emit("patch.rejected", {"patch_id": "p1", "rejection_code": "OVERLAP", "reason": "Conflicting duplicate operations on path 'main.py'"})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_scenario_s4_timeout(run_id: str = "golden_s4_timeout") -> List[Dict[str, Any]]:
    """S4: Command times out and is cleanly stopped."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "base_image": "python:3.11-slim"})
    emitter.emit("command.started", {"command_id": "c1", "command": "sleep 300", "timeout_s": 5})
    emitter.emit("command.finished", {"command_id": "c1", "exit_code": -1, "status": "TIMEOUT", "error_category": "TIMEOUT", "fingerprint": "TIMEOUT:001"})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_scenario_s5_missing_credential(run_id: str = "golden_s5_credential") -> List[Dict[str, Any]]:
    """S5: App requires external API key / secret -> BLOCKED."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "base_image": "python:3.11-slim"})
    emitter.emit("command.started", {"command_id": "c1", "command": "python3 main.py"})
    emitter.emit("command.finished", {"command_id": "c1", "exit_code": 1, "status": "FAILED", "error_category": "ENV_VAR_MISSING", "fingerprint": "ENV_VAR_MISSING:key_api"})
    emitter.emit("verify.started", {"run_id": run_id})
    emitter.emit("check.finished", {"check_type": "env", "verdict": "BLOCKED", "blocker_reason": "Missing required secret: OPENAI_API_KEY"})
    emitter.emit("verify.finished", {"overall_status": "BLOCKED", "completion_level": "NONE", "is_green": False})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_scenario_s6_budget_exhausted(run_id: str = "golden_s6_budget") -> List[Dict[str, Any]]:
    """S6: Step ceiling (25 steps) reached -> BUDGET_EXHAUSTED."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "base_image": "python:3.11-slim"})
    for i in range(1, 26):
        emitter.emit("command.started", {"command_id": f"c{i}", "command": f"echo step {i}"})
        emitter.emit("command.finished", {"command_id": f"c{i}", "exit_code": 0, "status": "SUCCESS"})
    emitter.emit("budget.exhausted", {"run_id": run_id, "steps_used": 25, "reason": "Step ceiling reached (25/25 steps)"})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_scenario_s7_sandbox_error(run_id: str = "golden_s7_sandbox_error") -> List[Dict[str, Any]]:
    """S7: Sandbox failure (container killed externally) -> SANDBOX_ERROR."""
    store = EventStore(run_id, base_dir="runs")
    emitter = EventEmitter(run_id, store=store)

    emitter.emit("run.provisioned", {"run_id": run_id, "base_image": "python:3.11-slim"})
    emitter.emit("command.started", {"command_id": "c1", "command": "python app.py"})
    emitter.emit("command.finished", {"command_id": "c1", "exit_code": -1, "status": "SANDBOX_ERROR", "error_category": "UNKNOWN", "stderr": "Container stopped unexpectedly"})
    emitter.emit("run.closed", {"run_id": run_id})

    return store.read_all()


def generate_all_golden_scenarios(output_dir: str = "tests/mocks/orchestrator/golden") -> None:
    os.makedirs(output_dir, exist_ok=True)
    scenarios = {
        "s1_happy_path.json": generate_scenario_s1_happy_path(),
        "s2_stale_snapshot.json": generate_scenario_s2_stale_snapshot(),
        "s3_overlap.json": generate_scenario_s3_overlap(),
        "s4_timeout.json": generate_scenario_s4_timeout(),
        "s5_missing_credential.json": generate_scenario_s5_missing_credential(),
        "s6_budget_exhausted.json": generate_scenario_s6_budget_exhausted(),
        "s7_sandbox_error.json": generate_scenario_s7_sandbox_error(),
    }
    for filename, events in scenarios.items():
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2)


if __name__ == "__main__":
    generate_all_golden_scenarios()
