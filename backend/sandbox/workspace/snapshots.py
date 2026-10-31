"""Snapshot store and content-hash tree snapshot tracking."""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import Dict, List, Optional


class SnapshotStore:
    """Manages content-hash snapshots of workspace files."""

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.store_dir = os.path.join(workspace_path, ".shadow_snapshots")
        self.snapshots: List[str] = []

    def compute_tree_hash(self) -> str:
        """Compute deterministic SHA-256 hash of all files in workspace."""
        hasher = hashlib.sha256()
        file_hashes: Dict[str, str] = {}

        for root, dirs, files in os.walk(self.workspace_path):
            # Ignore git and shadow snapshot directories
            dirs[:] = [d for d in dirs if d not in {".git", ".shadow_snapshots", "__pycache__", ".venv"}]
            for f in sorted(files):
                filepath = os.path.join(root, f)
                relpath = os.path.relpath(filepath, self.workspace_path).replace("\\", "/")
                try:
                    with open(filepath, "rb") as fp:
                        content = fp.read()
                        fhash = hashlib.sha256(content).hexdigest()
                        file_hashes[relpath] = fhash
                except Exception:
                    pass

        for path in sorted(file_hashes.keys()):
            hasher.update(f"{path}:{file_hashes[path]}\n".encode("utf-8"))

        return hasher.hexdigest()[:16]

    def create_snapshot(self) -> str:
        """Create a new snapshot and store files in shadow storage."""
        snap_id = self.compute_tree_hash()
        snap_path = os.path.join(self.store_dir, snap_id)

        if not os.path.exists(snap_path):
            os.makedirs(snap_path, exist_ok=True)
            for root, dirs, files in os.walk(self.workspace_path):
                dirs[:] = [d for d in dirs if d not in {".git", ".shadow_snapshots", "__pycache__", ".venv"}]
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(src, self.workspace_path)
                    dst = os.path.join(snap_path, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        pass

        if not self.snapshots or self.snapshots[-1] != snap_id:
            self.snapshots.append(snap_id)

        return snap_id

    def restore_snapshot(self, snap_id: str) -> bool:
        """Restore workspace to a previous snapshot."""
        snap_path = os.path.join(self.store_dir, snap_id)
        if not os.path.exists(snap_path):
            return False

        # Clear workspace files except .git and .shadow_snapshots
        for root, dirs, files in os.walk(self.workspace_path, topdown=False):
            dirs[:] = [d for d in dirs if d not in {".git", ".shadow_snapshots"}]
            for f in files:
                filepath = os.path.join(root, f)
                rel = os.path.relpath(filepath, self.workspace_path)
                if not rel.startswith(".git") and not rel.startswith(".shadow_snapshots"):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass

        # Copy files back from snapshot
        for root, dirs, files in os.walk(snap_path):
            for f in files:
                src = os.path.join(root, f)
                rel = os.path.relpath(src, snap_path)
                dst = os.path.join(self.workspace_path, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

        return True
