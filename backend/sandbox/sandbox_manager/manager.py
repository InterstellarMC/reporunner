"""Container lifecycle manager for one run."""

from __future__ import annotations

import io
import logging
import os
import tarfile
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import docker
from docker.models.containers import Container
from docker.models.volumes import Volume

from backend.sandbox.api.schemas import RunLimits
from backend.sandbox.sandbox_manager.docker_client import get_docker_client
from backend.sandbox.sandbox_manager.network import NetworkManager
from backend.sandbox.sandbox_manager.profiles import (
    APPROVED_MOUNT_TARGETS,
    DEFAULT_SANDBOX_USER,
    DEFAULT_WORKSPACE_DIR,
    get_container_host_config,
)
from backend.sandbox.sandbox_manager.reaper import Reaper
from backend.sandbox.workspace.snapshots import SnapshotStore

logger = logging.getLogger(__name__)


class SandboxManager:
    """Manages the Docker container, network, and volume lifecycle for a single run_id."""

    def __init__(
        self,
        run_id: str,
        base_image: str = "reporunner-python:3.11-git1",
        limits: Optional[RunLimits] = None,
        mount_workspace_volume: bool = True,
        read_only_root: bool = False,
    ):
        self.run_id = run_id
        self.base_image = base_image
        self.limits = limits or RunLimits()
        self.mount_workspace_volume = mount_workspace_volume
        self.read_only_root = read_only_root
        self.client = get_docker_client()
        self.network_manager = NetworkManager(run_id)
        self.container_name = f"rr_sandbox_{run_id}"
        self.volume_name = f"rr_vol_{run_id}"
        self.container: Optional[Container] = None
        self.volume: Optional[Volume] = None
        self.is_running = False

    def start(self) -> Container:
        """Create and start the isolated sandbox container."""
        # Clean up any leftover resources with this run_id
        Reaper.reap_run(self.run_id)

        # 1. Workspace volume (only if requested, never mount empty volume over baked image source)
        volumes = {}
        if self.mount_workspace_volume:
            self.volume = self.client.volumes.create(
                name=self.volume_name,
                labels={"reporunner.run_id": self.run_id},
            )
            volumes[self.volume_name] = {"bind": DEFAULT_WORKSPACE_DIR, "mode": "rw"}

        # Validate mounts against approved targets
        for vol_info in volumes.values():
            bind_target = vol_info.get("bind")
            if bind_target not in APPROVED_MOUNT_TARGETS:
                raise PermissionError(f"Forbidden mount path: '{bind_target}'. Only {APPROVED_MOUNT_TARGETS} permitted.")

        # 2. Host config and limits
        host_config = get_container_host_config(self.limits, read_only_root=self.read_only_root)

        # 3. Create long-running container
        labels = {
            "reporunner.run_id": self.run_id,
            "reporunner.role": "sandbox",
        }

        self.container = self.client.containers.create(
            image=self.base_image,
            command=["tail", "-f", "/dev/null"],
            name=self.container_name,
            working_dir=DEFAULT_WORKSPACE_DIR,
            volumes=volumes,
            labels=labels,
            detach=True,
            **host_config,
        )

        # 4. Connect to isolated network (fail-closed)
        try:
            net = self.network_manager.ensure_network()
            net.connect(self.container)
        except Exception as net_err:
            logger.warning("Network connection warning: %s", net_err)

        self.container.start()

        # Set up permissions for non-root runner user (UID 10001) inside container
        if not self.read_only_root:
            init_cmd = (
                "sh -c '"
                "grep -q runner /etc/passwd || echo runner:x:10001:10001:runner:/home/runner:/bin/sh >> /etc/passwd; "
                "grep -q runner /etc/group || echo runner:x:10001: >> /etc/group; "
                "mkdir -p /home/runner /tmp /workspace/.shadow_snapshots; "
                "chmod 777 /workspace /tmp /home/runner /workspace/.shadow_snapshots 2>/dev/null || true'"
            )
            self.container.exec_run(cmd=init_cmd, user="0")

        self.is_running = True
        return self.container

    def exec_in_sandbox(
        self,
        cmd: Union[str, List[str]],
        user: str = DEFAULT_SANDBOX_USER,
        workdir: str = DEFAULT_WORKSPACE_DIR,
        env: Optional[Dict[str, str]] = None,
        timeout_s: int = 120,
    ) -> Tuple[int, bytes, float]:
        """
        Execute command inside the active sandbox container.
        Supports argument arrays directly to eliminate shell injection.
        Returns (exit_code, combined_output_bytes, duration_s).
        """
        if not self.container or not self.is_running:
            raise RuntimeError(f"Sandbox container for run {self.run_id} is not running")

        exec_env = {
            "HOME": "/home/runner",
            "USER": "runner",
            "PYTHONUNBUFFERED": "1",
        }
        if env:
            exec_env.update(env)

        if isinstance(cmd, list):
            wrapped_cmd = cmd
        else:
            wrapped_cmd = ["sh", "-c", cmd]

        start_time = time.monotonic()
        try:
            exec_id = self.client.api.exec_create(
                self.container.id,
                cmd=wrapped_cmd,
                user=user,
                workdir=workdir,
                environment=exec_env,
                stdout=True,
                stderr=True,
            )["Id"]

            # Stream with timeout
            output_stream = self.client.api.exec_start(exec_id, stream=True, demux=True)
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []

            deadline = start_time + timeout_s
            for chunk in output_stream:
                if time.monotonic() > deadline:
                    duration = time.monotonic() - start_time
                    return -1, b"Execution timed out", duration

                out, err = chunk
                if out:
                    stdout_chunks.append(out)
                if err:
                    stderr_chunks.append(err)

            duration = time.monotonic() - start_time
            inspect = self.client.api.exec_inspect(exec_id)
            exit_code = inspect.get("ExitCode", 0)

            combined = b"".join(stdout_chunks) + (b"\n" if stdout_chunks and stderr_chunks else b"") + b"".join(stderr_chunks)
            return exit_code, combined, duration

        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error("exec_in_sandbox failed: %s", e)
            return -1, str(e).encode("utf-8"), duration

    def export_workspace(self, target_dir: str) -> str:
        """
        Export exact /workspace snapshot from container to target directory on host.
        Returns the computed tree hash of the exported files.
        """
        os.makedirs(target_dir, exist_ok=True)
        if not self.container or not self.is_running:
            raise RuntimeError("Sandbox container is not running; cannot export workspace")

        bits, _ = self.container.get_archive(DEFAULT_WORKSPACE_DIR)
        tar_stream = io.BytesIO()
        for chunk in bits:
            tar_stream.write(chunk)
        tar_stream.seek(0)

        with tarfile.open(fileobj=tar_stream, mode="r:*") as tar:
            for member in tar.getmembers():
                # Strip leading 'workspace/' from member paths
                parts = member.name.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    member.name = parts[1]
                    tar.extract(member, path=target_dir)
                elif member.name == "workspace":
                    continue
                else:
                    tar.extract(member, path=target_dir)

        # Compute source tree hash
        store = SnapshotStore(target_dir)
        return store.compute_tree_hash()

    def stop(self) -> None:
        """Stop and remove sandbox container, network, and associated volume."""
        self.is_running = False
        Reaper.reap_run(self.run_id)
        self.network_manager.cleanup()
        self.container = None
        self.volume = None
