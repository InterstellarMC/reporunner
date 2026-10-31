"""Clean rebuild service: builds Docker image from generated Dockerfile."""

from __future__ import annotations

import logging
import os
import time
from typing import Tuple
from backend.sandbox.sandbox_manager.docker_client import get_docker_client

logger = logging.getLogger(__name__)


class CleanBuilder:
    """Builds a fresh Docker image from generated Dockerfile."""

    @classmethod
    def build_from_path(
        cls,
        build_context_path: str,
        dockerfile_path: str,
        tag: str,
        timeout_s: int = 300,
    ) -> Tuple[bool, str, float]:
        """
        Build a clean Docker image.
        Returns (success, log_output, duration_s).
        """
        client = get_docker_client()
        start = time.monotonic()
        logs: list[str] = []

        try:
            rel_dockerfile = os.path.relpath(dockerfile_path, build_context_path)
            image, build_logs = client.images.build(
                path=build_context_path,
                dockerfile=rel_dockerfile,
                tag=tag,
                rm=True,
                forcerm=True,
            )
            for chunk in build_logs:
                if "stream" in chunk:
                    logs.append(chunk["stream"])

            duration = time.monotonic() - start
            return True, "".join(logs), duration

        except Exception as e:
            duration = time.monotonic() - start
            logger.error("Clean build failed: %s", e)
            return False, str(e), duration
