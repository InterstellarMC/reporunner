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
        if commit_sha is not None:
            commit_sha = commit_sha.strip()
            if not commit_sha or not HEX_SHA_REGEX.match(commit_sha):
                return False, f"Security check failed: commit_sha '{commit_sha}' is not a valid hex SHA"

        # 3. Verify Git is pre-installed in the base image (NO apt-get inside restricted container)
        check_code, _, _ = self.sandbox.exec_in_sandbox(["git", "--version"], timeout_s=10)
        if check_code != 0:
            return False, "Base image does not contain git. Apt-get in restricted container is forbidden."

        # 4. Clone into a temporary directory because /workspace
        # already contains RepoRunner's internal .shadow_snapshots directory.
        temp_clone_dir = "/tmp/reporunner-repository-clone"
        try:
            cleanup_code, cleanup_out, _ = self.sandbox.exec_in_sandbox(
                ["rm", "-rf", temp_clone_dir],
                user="10001:10001",
                timeout_s=10,
            )
            clone_args = ["git", "clone", repo_url, temp_clone_dir]
            exit_code, out, _ = self.sandbox.exec_in_sandbox(
                clone_args,
                user="10001:10001",
                timeout_s=180,
            )
            if exit_code != 0:
                return False, (
                    f"Git clone failed (exit {exit_code}): "
                    f"{out.decode('utf-8', errors='replace')}"
                )

            # 5. Check out pinned commit if specified (executed as unprivileged user)
            if commit_sha:
                checkout_args = ["git", "checkout", commit_sha]
                c_code, c_out, _ = self.sandbox.exec_in_sandbox(
                    checkout_args,
                    workdir=temp_clone_dir,
                    user="10001:10001",
                    timeout_s=30,
                )
                if c_code != 0:
                    # If commit not in shallow clone, fetch all and retry
                    self.sandbox.exec_in_sandbox(
                        ["git", "fetch", "--all", "--tags"],
                        workdir=temp_clone_dir,
                        user="10001:10001",
                        timeout_s=60,
                    )
                    c_code, c_out, _ = self.sandbox.exec_in_sandbox(
                        checkout_args,
                        workdir=temp_clone_dir,
                        user="10001:10001",
                        timeout_s=30,
                    )
                    if c_code != 0:
                        return False, (
                            f"Git checkout {commit_sha} failed: "
                            f"{c_out.decode('utf-8', errors='replace')}"
                        )

            # 6. Copy complete repository into /workspace (preserving .shadow_snapshots)
            cp_code, cp_out, _ = self.sandbox.exec_in_sandbox(
                ["cp", "-R", f"{temp_clone_dir}/.", "/workspace/"],
                user="10001:10001",
                timeout_s=30,
            )
            if cp_code != 0:
                return False, (
                    f"Failed to copy repository to /workspace: "
                    f"{cp_out.decode('utf-8', errors='replace')}"
                )
        finally:
            # Clean up temporary clone directory with fallback to root if ownership mismatch
            cleanup_res, _, _ = self.sandbox.exec_in_sandbox(
                ["rm", "-rf", temp_clone_dir],
                user="10001:10001",
                timeout_s=10,
            )
            if cleanup_res != 0:
                self.sandbox.exec_in_sandbox(
                    ["rm", "-rf", temp_clone_dir],
                    user="0",
                    timeout_s=10,
                )

        # 6. Verify actual HEAD SHA
        sha_args = ["git", "-c", "safe.directory=/workspace", "rev-parse", "HEAD"]
        sha_code, sha_out, _ = self.sandbox.exec_in_sandbox(sha_args, workdir="/workspace", user="10001:10001", timeout_s=10)
        if sha_code != 0:
            return False, f"Failed to resolve checked-out commit SHA: {sha_out.decode('utf-8', errors='replace').strip()}"

        self.pinned_sha = sha_out.decode("utf-8").strip()

        # Enforce exact match if commit_sha was specified
        if commit_sha:
            expected_lower = commit_sha.lower()
            actual_lower = self.pinned_sha.lower()
            if len(expected_lower) == 40:
                if actual_lower != expected_lower:
                    return False, f"Pinned SHA mismatch: expected {commit_sha}, got {self.pinned_sha}"
            else:
                # Short SHA (7-39 chars): must match prefix and unambiguously resolve in git
                if not actual_lower.startswith(expected_lower):
                    return False, f"Pinned short SHA mismatch: expected prefix {commit_sha}, got {self.pinned_sha}"
                verify_args = ["git", "-c", "safe.directory=/workspace", "rev-parse", "--verify", f"{commit_sha}^{{commit}}"]
                v_code, v_out, _ = self.sandbox.exec_in_sandbox(
                    verify_args, workdir="/workspace", user="10001:10001", timeout_s=10
                )
                if v_code != 0:
                    return False, f"Commit SHA '{commit_sha}' is ambiguous or unresolvable in repository"
                resolved_sha = v_out.decode("utf-8").strip()
                if resolved_sha.lower() != actual_lower:
                    return False, f"Short SHA '{commit_sha}' resolved to {resolved_sha}, but HEAD is at {self.pinned_sha}"

        # 7. Compute snapshot hash
        snap_cmd = (
            "python3 -c \""
            "import os, hashlib; "
            "h = hashlib.sha256(); "
            "files = sorted([os.path.join(r, f) for r, _, fs in os.walk('/workspace') if '.git' not in r and '.shadow_snapshots' not in r for f in fs]); "
            "[h.update(open(f, 'rb').read()) for f in files]; "
            "print(h.hexdigest()[:16])\""
        )
        _, snap_out, _ = self.sandbox.exec_in_sandbox(snap_cmd, workdir="/workspace", timeout_s=30)
        self.current_snapshot_id = snap_out.decode("utf-8").strip() or "init_snap"

        # Apply least-privilege permissions: runner user owns /workspace, directories 755, snapshots 700 (NEVER 777)
        self.sandbox.exec_in_sandbox(["chmod", "755", "/workspace"], user="10001:10001", timeout_s=10)
        self.sandbox.exec_in_sandbox(["chmod", "700", "/workspace/.shadow_snapshots"], user="10001:10001", timeout_s=10)

        return True, self.pinned_sha
