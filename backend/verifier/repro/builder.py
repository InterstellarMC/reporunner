"""Clean rebuild service: builds Docker image from generated Dockerfile with resource limits and timeouts."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple
from backend.sandbox.sandbox_manager.docker_client import get_docker_client

logger = logging.getLogger(__name__)


class CleanBuilder:
    """Builds a fresh Docker image from generated Dockerfile and source context."""

    @classmethod
    def build_from_path(
        cls,
        build_context_path: str,
        dockerfile_path: str,
        tag: str,
        timeout_s: int = 300,
        mem_limit_bytes: int = 4 * 1024 * 1024 * 1024,
    ) -> Tuple[bool, str, float, Optional[str]]:
        """
        Build a clean Docker image with enforced timeout and limits.
        Returns (success, log_output, duration_s, image_digest).
        """
        start = time.monotonic()
        logs: list[str] = []
        client = get_docker_client()

        # Check 1: Empty build context verification
        if not os.path.exists(build_context_path):
            return False, f"Build context path does not exist: {build_context_path}", 0.0, None

        source_files = [
            f for f in os.listdir(build_context_path)
            if f not in {"Dockerfile", ".dockerignore"}
        ]
        if not source_files:
            return False, "Empty build context: no source files found in context path", 0.0, None

        if not os.path.exists(dockerfile_path):
            return False, f"Dockerfile not found at {dockerfile_path}", 0.0, None

        try:
            rel_dockerfile = os.path.relpath(dockerfile_path, build_context_path)
            image, build_logs = client.images.build(
                path=build_context_path,
                dockerfile=rel_dockerfile,
                tag=tag,
                rm=True,
                forcerm=True,
                timeout=timeout_s,
                container_limits={"memory": mem_limit_bytes},
            )

            deadline = start + timeout_s
            for chunk in build_logs:
                if time.monotonic() > deadline:
                    cls._cleanup_tag(client, tag)
                    duration = time.monotonic() - start
                    return False, f"Image build timed out after {timeout_s}s", duration, None
                if "stream" in chunk:
                    logs.append(chunk["stream"])

            duration = time.monotonic() - start
            image_digest = image.attrs.get("RepoDigests", [image.id])[0] if image else None
            return True, "".join(logs), duration, image_digest

        except Exception as e:
            duration = time.monotonic() - start
            logger.error("Clean build failed for tag %s: %s", tag, e)
            cls._cleanup_tag(client, tag)
            return False, str(e), duration, None

    @classmethod
    def _cleanup_tag(cls, client, tag: str) -> None:
        """Attempt to remove tag/image on build failure."""
        try:
            client.images.remove(tag, force=True)
        except Exception:
            pass
