"""Path validation, normalization, and scope enforcement."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

FORBIDDEN_DIR_PREFIXES = {
    ".git",
    ".shadow_snapshots",
    "/proc",
    "/sys",
    "/dev",
    "/etc",
}


def normalize_relpath(path: str) -> str:
    """Normalize path into a standard forward-slash relative path."""
    norm = os.path.normpath(path).replace("\\", "/")
    # Strip leading slash if any
    if norm.startswith("/"):
        norm = norm.lstrip("/")
    return norm


def is_path_safe(rel_path: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that relative path does not escape workspace or hit protected directories.
    Returns (is_safe, error_reason).
    """
    if not rel_path or not rel_path.strip():
        return False, "Empty path"

    norm = normalize_relpath(rel_path)

    # Check path traversal
    if norm == ".." or norm.startswith("../") or "/../" in norm:
        return False, "Path traversal attempt ('..')"

    # Check protected directories
    parts = norm.split("/")
    for protected in FORBIDDEN_DIR_PREFIXES:
        p_clean = protected.strip("/")
        if parts[0] == p_clean or norm.startswith(f"{p_clean}/"):
            return False, f"Access to protected path '{protected}' is forbidden"

    return True, None


def check_scope(paths: List[str], allowed_scopes: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Verify that all target paths fall within the declared scope.
    An empty scope means all workspace paths are allowed.
    """
    if not allowed_scopes:
        return True, None

    normalized_scopes = [normalize_relpath(s) for s in allowed_scopes]

    for p in paths:
        norm_p = normalize_relpath(p)
        in_scope = False
        for scope in normalized_scopes:
            if norm_p == scope or norm_p.startswith(f"{scope}/"):
                in_scope = True
                break
        if not in_scope:
            return False, f"Path '{norm_p}' is outside declared scope ({allowed_scopes})"

    return True, None
