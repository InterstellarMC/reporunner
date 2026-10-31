"""Genuine benchmark runner executing real repositories through the ExecutionService adapter."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
import yaml

from benchmark.adapter import ExecutionAdapter, RealExecutionAdapter
from benchmark.schemas import BenchmarkEntry, BenchmarkResult

logger = logging.getLogger(__name__)

# Enforce exactly 40 hexadecimal characters for full pinned SHAs
HEX_SHA_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")


class BenchmarkRunner:
    """
    Executes benchmark suites against genuine GitHub repositories using
    the real RepoRunner sandbox, command engine, independent verifier,
    and fresh-build reproducibility validator.
    """

    def __init__(
        self,
        manifest_path: str = "benchmark/manifest.yaml",
        results_dir: str = "benchmark/results",
        adapter: Optional[ExecutionAdapter] = None,
    ):
        self.manifest_path = manifest_path
        self.results_dir = results_dir
        self.adapter = adapter or RealExecutionAdapter()
        os.makedirs(self.results_dir, exist_ok=True)
        self.entries: List[BenchmarkEntry] = self._load_manifest()

    def _load_manifest(self) -> List[BenchmarkEntry]:
        """Parse and validate manifest entries from YAML using yaml.safe_load."""
        if not os.path.exists(self.manifest_path):
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest_dict = yaml.safe_load(f)

        if not manifest_dict or "benchmarks" not in manifest_dict:
            raise ValueError(f"Invalid manifest: missing 'benchmarks' root list")

        raw_entries = manifest_dict["benchmarks"]
        entries: List[BenchmarkEntry] = []

        for data in raw_entries:
            entry_id = str(data.get("id", ""))
            repo_url = str(data.get("repo_url", ""))
            commit_sha = str(data.get("commit_sha", ""))

            # Strict validation: repo_url must start with http/https
            if not repo_url or not (repo_url.startswith("http://") or repo_url.startswith("https://")):
                raise ValueError(f"Entry {entry_id} has invalid or missing repo_url: '{repo_url}'")

            # Strict validation: commit_sha must be exactly 40 hex characters
            if not commit_sha or len(commit_sha) != 40 or not HEX_SHA_REGEX.match(commit_sha):
                raise ValueError(
                    f"Entry {entry_id} must have a 40-character hexadecimal commit SHA, got: '{commit_sha}'"
                )

            entry = BenchmarkEntry(
                id=entry_id,
                name=data.get("name", entry_id),
                stack=data.get("stack", "python"),
                framework=data.get("framework"),
                fault=data.get("fault"),
                verification_type=data.get("verification_type", "pytest"),
                http_port=data.get("http_port"),
                cli_command=data.get("cli_command"),
                expected_outcome=data.get("expected_outcome", "GREEN"),
                repo_url=repo_url,
                commit_sha=commit_sha,
                blocker_type=data.get("blocker_type"),
                blocker_name=data.get("blocker_name"),
            )
            entries.append(entry)

        return entries

    def validate_manifest(self) -> bool:
        """
        Validate that the manifest has exactly 10 repos (7 fixable, 3 blocked)
        and every repository has a valid URL and pinned 40-char commit SHA.
        """
        if len(self.entries) != 10:
            return False
        fixable = [e for e in self.entries if e.expected_outcome == "GREEN"]
        blocked = [e for e in self.entries if e.expected_outcome == "BLOCKED"]
        if len(fixable) != 7 or len(blocked) != 3:
            return False

        for e in self.entries:
            if not e.repo_url or not e.commit_sha:
                return False
            if len(e.commit_sha) != 40 or not HEX_SHA_REGEX.match(e.commit_sha):
                return False

        return True

    def run_single(self, entry_id: str) -> BenchmarkResult:
        """
        Execute a single benchmark entry through the execution adapter.
        Expected outcome is NOT used to determine actual outcome.
        """
        entry = next((e for e in self.entries if e.id == entry_id), None)
        if not entry:
            raise ValueError(f"Entry {entry_id} not found in manifest")

        wall_start = time.monotonic()
        result = self.adapter.execute(entry)
        wall_elapsed = time.monotonic() - wall_start

        # Guarantee wall clock time is non-zero and accurately recorded
        if result.wall_clock_seconds <= 0.0:
            result.wall_clock_seconds = round(wall_elapsed, 3)
            result.wall_clock_duration = round(wall_elapsed, 3)

        # Ensure match is strictly comparison of actual against expected
        result.match = (result.actual_outcome == entry.expected_outcome)

        # Write result to results/
        result_file = os.path.join(self.results_dir, f"{entry_id}_result.json")
        with open(result_file, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json(indent=2))

        return result

    def run_infrastructure_mode(self, entry_id: str) -> Dict[str, Any]:
        """Backward-compatibility facade returning dictionary result."""
        result = self.run_single(entry_id)
        d = result.model_dump()
        d["expected"] = result.expected_outcome
        d["actual"] = result.actual_outcome
        d["reproducibility_green"] = (result.actual_outcome == "GREEN" and result.reproducibility_verdict is True)
        d["steps_used"] = result.execution_steps
        return d

    def run_all(self) -> List[BenchmarkResult]:
        """Execute all manifest benchmarks and return evidence-backed results."""
        results: List[BenchmarkResult] = []
        for entry in self.entries:
            res = self.run_single(entry.id)
            results.append(res)
        return results
