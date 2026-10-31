"""Sandbox security profiles and resource configurations."""

from __future__ import annotations

from typing import Any, Dict, List
from backend.sandbox.api.schemas import RunLimits

DEFAULT_SANDBOX_USER = "10001:10001"
DEFAULT_WORKSPACE_DIR = "/workspace"


def get_container_host_config(limits: RunLimits) -> Dict[str, Any]:
    """Build secure host config parameters for Docker container creation."""
    mem_bytes = 4 * 1024 * 1024 * 1024  # 4 GiB
    if limits.memory_limit.endswith("g"):
        mem_bytes = int(float(limits.memory_limit[:-1]) * 1024 * 1024 * 1024)
    elif limits.memory_limit.endswith("m"):
        mem_bytes = int(float(limits.memory_limit[:-1]) * 1024 * 1024)

    return {
        "mem_limit": mem_bytes,
        "memswap_limit": mem_bytes,  # swap equal to memory (no unbounded swap)
        "nano_cpus": int(limits.cpu_limit * 1_000_000_000),
        "pids_limit": limits.pids_limit,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
