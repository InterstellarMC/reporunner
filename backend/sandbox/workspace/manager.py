"""Workspace manager for secure repository cloning and snapshot tracking."""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Optional, Tuple
from backend.sandbox.sandbox_manager.manager import SandboxManager

logger = logging.getLogger(__name__)

HEX_SHA_REGEX = re.compile(r"^[0-9a-fA-F]{7,40}$")
FORBIDDEN_URL_CHARS = {";", "&", "|", "`", "$", "(", ")", "<", ">", "\n", "\r", " "}


def validate_git_url(url: str) -> Tuple[bool, str]:
    """
    Strict URL validation rejecting dangerous schemes, characters, and command injection vectors.
    """
    if not url or not isinstance(url, str):
        return False, "Repository URL must be a non-empty string"
    url = url.strip()
    if url.startswith("-"):
        return False, "Repository URL cannot start with a hyphen"
    if any(c in url for c in FORBIDDEN_URL_CHARS):
        return False, "Repository URL contains forbidden shell metacharacters"

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return False, f"Malformed URL: {e}"

    if parsed.scheme not in {"http", "https"}:
        return False, f"Forbidden URL scheme '{parsed.scheme}': only http and https are permitted"
    if not parsed.netloc:
        return False, "Repository URL must contain a valid domain/host"

    return True, ""


class WorkspaceManager:
    """Handles secure git cloning and workspace state inside the sandbox."""

    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox
        self.pinned_sha: Optional[str] = None
        self.current_snapshot_id: Optional[str] = None

    def clone_repository(
        self,
        repo_url: str,
        commit_sha: Optional[str] = None,
        depth: int = 1,
    ) -> Tuple[bool, str]:
        """
        Securely clone the repository into /workspace using direct argument arrays.
        Never interpolates URLs or SHAs into a shell string.
        """
        # 1. Validate URL
        valid_url, url_err = validate_git_url(repo_url)
        if not valid_url:
            return False, f"Security check failed: {url_err}"

        # 2. Validate commit SHA if provided
        if commit_sha:
            commit_sha = commit_sha.strip()
            if not HEX_SHA_REGEX.match(commit_sha):
                return False, f"Security check failed: commit_sha '{commit_sha}' is not a valid hex SHA"

        # 3. Verify Git is pre-installed in the base image (NO apt-get inside restricted container)
        check_code, _, _ = self.sandbox.exec_in_sandbox(["git", "--version"], timeout_s=10)
        if check_code != 0:
            return False, "Base image does not contain git. Apt-get in restricted container is forbidden."

        # 4. Execute git clone using argument array (no shell interpolation)
        clone_args = ["git", "clone", repo_url, "/workspace"]
        exit_code, out, _ = self.sandbox.exec_in_sandbox(clone_args, user="0", timeout_s=180)
        if exit_code != 0:
            return False, f"Git clone failed (exit {exit_code}): {out.decode('utf-8', errors='replace')}"

        # 5. Check out pinned commit if specified
        if commit_sha:
            checkout_args = ["git", "checkout", commit_sha]
            c_code, c_out, _ = self.sandbox.exec_in_sandbox(checkout_args, workdir="/workspace", timeout_s=30)
            if c_code != 0:
                # If commit not in shallow clone, fetch all and retry
                self.sandbox.exec_in_sandbox(["git", "fetch", "--all"], workdir="/workspace", timeout_s=60)
                c_code, c_out, _ = self.sandbox.exec_in_sandbox(checkout_args, workdir="/workspace", timeout_s=30)
                if c_code != 0:
                    return False, f"Git checkout {commit_sha} failed: {c_out.decode('utf-8', errors='replace')}"

        # 6. Verify actual HEAD SHA
        sha_args = ["git", "rev-parse", "HEAD"]
        sha_code, sha_out, _ = self.sandbox.exec_in_sandbox(sha_args, workdir="/workspace", timeout_s=10)
        if sha_code != 0:
            return False, "Failed to resolve checked-out commit SHA"

        self.pinned_sha = sha_out.decode("utf-8").strip()

        # Enforce exact match if commit_sha was specified
        if commit_sha and not self.pinned_sha.lower().startswith(commit_sha.lower()):
            return False, f"Pinned SHA mismatch: expected {commit_sha}, got {self.pinned_sha}"

        # 7. Compute snapshot hash
        snap_cmd = (
            "python3 -c \""
            "import os, hashlib; "
            "h = hashlib.sha256(); "
            "files = sorted([os.path.join(r, f) for r, _, fs in os.walk('/workspace') if '.git' not in r for f in fs]); "
            "[h.update(open(f, 'rb').read()) for f in files]; "
            "print(h.hexdigest()[:16])\""
        )
        _, snap_out, _ = self.sandbox.exec_in_sandbox(snap_cmd, workdir="/workspace", timeout_s=30)
        self.current_snapshot_id = snap_out.decode("utf-8").strip() or "init_snap"

        # Ensure permissions for non-root runner user
        self.sandbox.exec_in_sandbox(["chmod", "-R", "777", "/workspace"], user="0", timeout_s=30)

        return True, self.pinned_sha
