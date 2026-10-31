"""Command security policy enforcement."""

from __future__ import annotations

import re
import shlex
from typing import Optional, Tuple

# Forbidden commands/binaries that are never permitted to execute
FORBIDDEN_BINARIES = {
    "docker",
    "dockerd",
    "sudo",
    "su",
    "mount",
    "umount",
    "nsenter",
    "chroot",
    "iptables",
    "ip6tables",
    "nft",
    "modprobe",
    "insmod",
    "rmmod",
    "systemctl",
    "service",
}

# Dangerous shell constructs / attempts to tamper with host or root FS
FORBIDDEN_PATTERNS = [
    re.compile(r"\brm\s+-[rfRF]*\s+/(?:\s|$|\*)"),  # rm -rf /
    re.compile(r">+\s*/dev/(?:sd[a-z]|nvme|kmem|mem|port)"),  # raw disk/memory access
    re.compile(r"/var/run/docker\.sock"),  # docker socket access
]


def check_command_policy(command: str) -> Tuple[bool, Optional[str]]:
    """
    Validate whether a command conforms to sandbox security policy.
    Returns (is_allowed, violation_reason).
    """
    if not command or not command.strip():
        return False, "Empty command"

    stripped = command.strip()

    # Pattern check
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(stripped):
            return False, f"Command matches forbidden pattern: {pattern.pattern}"

    # Token-level check
    try:
        # Split pipelines and multi-command statements
        sub_commands = re.split(r"[;&|]+", stripped)
        for sub_cmd in sub_commands:
            parts = shlex.split(sub_cmd.strip())
            if not parts:
                continue

            binary = parts[0]
            # Strip path prefix if given, e.g. /usr/bin/sudo -> sudo
            binary_name = binary.split("/")[-1].lower()
            if binary_name in FORBIDDEN_BINARIES:
                return False, f"Execution of forbidden binary '{binary_name}' is blocked"

    except Exception:
        # Fallback keyword scan if shlex parsing fails on shell syntax
        for forbidden in FORBIDDEN_BINARIES:
            if re.search(rf"\b{re.escape(forbidden)}\b", stripped, re.IGNORECASE):
                return False, f"Detected forbidden keyword '{forbidden}'"

    return True, None
