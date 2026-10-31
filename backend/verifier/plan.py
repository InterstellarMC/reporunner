"""Verification planning: detects project stack and builds non-negotiable baseline checks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from backend.verifier.ports import CommandRunner


class VerificationPlan:
    """Detects stack and ensures supervisor cannot remove baseline checks."""

    @classmethod
    def detect_stack(cls, runner: CommandRunner) -> Dict[str, Any]:
        """Inspect workspace files to identify technologies and required checks."""
        # Check files
        c_py, _, _ = runner.run("ls -1 /workspace/requirements*.txt /workspace/pyproject.toml /workspace/setup.py 2>/dev/null", timeout_s=5)
        has_python = (c_py == 0)

        c_node, _, _ = runner.run("test -f /workspace/package.json", timeout_s=5)
        has_node = (c_node == 0)

        # Look for web frameworks / ports
        c_web, out_web, _ = runner.run(
            "grep -rE 'flask|fastapi|uvicorn|django|express' /workspace/ 2>/dev/null | head -n 5",
            timeout_s=5,
        )
        is_web = (c_web == 0 and len(out_web.strip()) > 0)

        return {
            "is_python": has_python or not has_node,  # default python if ambiguous
            "is_node": has_node,
            "is_web": is_web,
        }
