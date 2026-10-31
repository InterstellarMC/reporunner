"""Evidence bundle generator: Dockerfile, RUN.md, evidence.json, and SHA-256 manifest."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from backend.evidence.dockerfile import generate_dockerfile
from backend.sandbox.api.schemas import CommandRequest, VerificationReport


class EvidenceGenerator:
    """Creates the complete evidence bundle for evaluators and judges."""

    def __init__(self, run_id: str, base_dir: str = "runs"):
        self.run_id = run_id
        self.evidence_dir = os.path.join(base_dir, run_id, "evidence")
        os.makedirs(self.evidence_dir, exist_ok=True)

    def generate_bundle(
        self,
        verification_report: VerificationReport,
        command_ledger: List[CommandRequest],
        base_image: str = "python:3.11-slim",
        run_cmd: str = "python3 app.py",
        port: Optional[int] = None,
        pinned_sha: Optional[str] = None,
        source_snapshot_hash: Optional[str] = None,
        image_digest: Optional[str] = None,
        build_logs: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate all evidence artifacts and return the manifest with SHA-256 hashes."""
        # 1. Dockerfile
        dockerfile_content = generate_dockerfile(
            base_image=base_image,
            command_ledger=command_ledger,
            run_cmd=run_cmd,
            port=port,
        )
        dockerfile_path = os.path.join(self.evidence_dir, "Dockerfile")
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content)

        # 2. RUN.md (one command to run it)
        run_md_content = self._build_run_md(run_id=self.run_id, run_cmd=run_cmd, port=port)
        run_md_path = os.path.join(self.evidence_dir, "RUN.md")
        with open(run_md_path, "w", encoding="utf-8") as f:
            f.write(run_md_content)

        # 3. evidence.json
        evidence_data = {
            "run_id": self.run_id,
            "pinned_sha": pinned_sha,
            "source_snapshot_hash": source_snapshot_hash,
            "image_digest": image_digest,
            "is_green": verification_report.is_green,
            "overall_status": verification_report.overall_status.value,
            "completion_level": verification_report.completion_level.value,
            "checks": [c.model_dump() for c in verification_report.checks],
            "commands_executed": len(command_ledger),
            "base_image": base_image,
            "build_logs_present": bool(build_logs),
        }
        evidence_json_path = os.path.join(self.evidence_dir, "evidence.json")
        with open(evidence_json_path, "w", encoding="utf-8") as f:
            json.dump(evidence_data, f, indent=2)

        # 4. Compute file hashes for manifest
        manifest = {}
        for fname in ["Dockerfile", "RUN.md", "evidence.json"]:
            fpath = os.path.join(self.evidence_dir, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as fp:
                    digest = hashlib.sha256(fp.read()).hexdigest()
                    manifest[fname] = digest

        manifest_path = os.path.join(self.evidence_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return {
            "evidence_dir": self.evidence_dir,
            "manifest": manifest,
            "is_green": verification_report.is_green,
            "source_snapshot_hash": source_snapshot_hash,
            "image_digest": image_digest,
        }

    def _build_run_md(self, run_id: str, run_cmd: str, port: Optional[int]) -> str:
        port_flag = f"-p {port}:{port} " if port else ""
        return f"""# RUN.md: How to run this verified project

This project was verified and packaged by **RepoRunner** (Team Astroworld, Agentathon 2026).

## Single command to run:

```sh
docker build -t reporunner-{run_id} .
docker run --rm {port_flag}reporunner-{run_id}
```

## Application command:
`{run_cmd}`
"""
