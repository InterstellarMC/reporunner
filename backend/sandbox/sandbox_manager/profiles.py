"""Sandbox security profiles and resource configurations."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from backend.sandbox.api.schemas import RunLimits
from backend.sandbox.sandbox_manager.docker_client import get_docker_client

logger = logging.getLogger(__name__)

DEFAULT_SANDBOX_USER = "10001:10001"
DEFAULT_WORKSPACE_DIR = "/workspace"
APPROVED_MOUNT_TARGETS = {DEFAULT_WORKSPACE_DIR}

# Pinned base images that have git and bootstrap tools installed
REPORUNNER_BASE_IMAGE_PYTHON = "reporunner-python:3.11-git1"
REPORUNNER_BASE_IMAGE_NODE = "reporunner-node:20-git1"


def get_container_host_config(limits: RunLimits, read_only_root: bool = False) -> Dict[str, Any]:
    """Build secure host config parameters for Docker container creation."""
    mem_bytes = 4 * 1024 * 1024 * 1024  # 4 GiB
    if limits.memory_limit.endswith("g"):
        mem_bytes = int(float(limits.memory_limit[:-1]) * 1024 * 1024 * 1024)
    elif limits.memory_limit.endswith("m"):
        mem_bytes = int(float(limits.memory_limit[:-1]) * 1024 * 1024)

    cfg: Dict[str, Any] = {
        "mem_limit": mem_bytes,
        "memswap_limit": mem_bytes,  # swap equal to memory (no unbounded swap)
        "nano_cpus": int(limits.cpu_limit * 1_000_000_000),
        "pids_limit": limits.pids_limit,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    if read_only_root:
        cfg["read_only"] = True
    return cfg


def get_base_image_digest(image_name: str) -> Optional[str]:
    """Retrieve the exact SHA256 digest or ID of the base image."""
    try:
        client = get_docker_client()
        img = client.images.get(image_name)
        if img.attrs.get("RepoDigests"):
            return img.attrs["RepoDigests"][0]
        return img.id
    except Exception as e:
        logger.warning("Could not retrieve image digest for %s: %s", image_name, e)
        return None
