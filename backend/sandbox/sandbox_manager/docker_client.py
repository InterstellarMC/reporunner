"""Docker client singleton and lifecycle helper."""

from __future__ import annotations

import logging
from typing import Optional
import docker
from docker.errors import DockerException

logger = logging.getLogger(__name__)

_CLIENT: Optional[docker.DockerClient] = None


def get_docker_client() -> docker.DockerClient:
    """Obtain or initialize the Docker Python SDK client."""
    global _CLIENT
    if _CLIENT is None:
        try:
            _CLIENT = docker.from_env()
            _CLIENT.ping()
        except DockerException as e:
            logger.error("Failed to connect to Docker daemon: %s", e)
            raise RuntimeError(f"Docker daemon is not accessible: {e}") from e
    return _CLIENT


def check_docker_available() -> bool:
    """Verify that the Docker daemon is responding."""
    try:
        client = get_docker_client()
        return bool(client.ping())
    except Exception:
        return False
