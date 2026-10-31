"""Sandbox security profiles and resource configurations."""

from __future__ import annotations

import logging
import re
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

# Reproducible base image build commands
BASE_IMAGE_BUILD_COMMANDS = {
    REPORUNNER_BASE_IMAGE_PYTHON: "docker build -t reporunner-python:3.11-git1 backend/sandbox/images/python",
    REPORUNNER_BASE_IMAGE_NODE: "docker build -t reporunner-node:20-git1 backend/sandbox/images/node",
}


def get_base_image_build_command(image_name: str) -> Optional[str]:
    """Return the exact command to reproducibly build a required base image."""
    return BASE_IMAGE_BUILD_COMMANDS.get(image_name)


def parse_memory_limit(mem_str: str) -> int:
    """
    Parse and validate a memory limit string (e.g. '512m', '4g') into bytes.
    Enforces minimum 64 MiB and rejects negative, zero, or unparseable values.
    """
    if not isinstance(mem_str, str) or not mem_str.strip():
        raise ValueError(f"Invalid memory_limit '{mem_str}': must be a non-empty string")
    s = mem_str.strip().lower()
    units = {
        "g": 1024 * 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
        "m": 1024 * 1024,
        "mb": 1024 * 1024,
        "k": 1024,
        "kb": 1024,
        "b": 1,
    }
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)$", s)
    if not match:
        raise ValueError(f"Cannot parse memory limit: '{mem_str}'")
    val_str, unit_str = match.groups()
    try:
        val = float(val_str)
    except ValueError as e:
        raise ValueError(f"Invalid numeric value in memory limit: '{mem_str}'") from e

    unit_multiplier = units.get(unit_str, 1)
    bytes_val = int(val * unit_multiplier)
    min_bytes = 64 * 1024 * 1024  # 64 MiB
    if bytes_val < min_bytes:
        raise ValueError(f"Memory limit {bytes_val} bytes is below the minimum allowed {min_bytes} bytes (64 MiB)")
    return bytes_val


def get_container_host_config(limits: RunLimits, read_only_root: bool = False) -> Dict[str, Any]:
    """
    Build secure host config parameters for Docker container creation with strict boundary checks.
    Guarantees limits cannot be bypassed by negative or zero values.
    """
    mem_bytes = parse_memory_limit(limits.memory_limit)

    if limits.cpu_limit <= 0.0:
        raise ValueError(f"cpu_limit must be positive, got {limits.cpu_limit}")
    if limits.pids_limit <= 0:
        raise ValueError(f"pids_limit must be positive, got {limits.pids_limit}")

    cfg: Dict[str, Any] = {
        "mem_limit": mem_bytes,
        "memswap_limit": mem_bytes,  # swap equal to memory (no unbounded swap)
        "nano_cpus": int(limits.cpu_limit * 1_000_000_000),
        "pids_limit": limits.pids_limit,
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "FOWNER", "DAC_OVERRIDE"],
        "security_opt": ["no-new-privileges:true"],
    }
    if read_only_root:
        cfg["read_only"] = True
        cfg["tmpfs"] = {"/tmp": "rw,noexec,nosuid,size=64m"}
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
