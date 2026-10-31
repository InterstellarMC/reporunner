"""Orphan and TTL cleanup reaper for Docker resources."""

from __future__ import annotations

import logging
from typing import List
from backend.sandbox.sandbox_manager.docker_client import get_docker_client

logger = logging.getLogger(__name__)


class Reaper:
    """Finds and removes stale containers, volumes, and networks labeled reporunner."""

    @classmethod
    def reap_run(cls, run_id: str) -> None:
        """Remove all resources belonging to a specific run_id."""
        client = get_docker_client()

        # Stop and remove containers
        try:
            containers = client.containers.list(
                all=True, filters={"label": f"reporunner.run_id={run_id}"}
            )
            for container in containers:
                try:
                    container.stop(timeout=2)
                except Exception:
                    pass
                try:
                    container.remove(force=True, v=True)
                except Exception as e:
                    logger.warning("Failed to remove container %s: %s", container.id, e)
        except Exception as e:
            logger.debug("Error listing run containers: %s", e)

        # Remove volumes
        try:
            volumes = client.volumes.list(filters={"label": f"reporunner.run_id={run_id}"})
            for volume in volumes:
                try:
                    volume.remove(force=True)
                except Exception as e:
                    logger.warning("Failed to remove volume %s: %s", volume.id, e)
        except Exception as e:
            logger.debug("Error listing run volumes: %s", e)

        # Remove networks
        try:
            networks = client.networks.list(filters={"label": f"reporunner.run_id={run_id}"})
            for network in networks:
                try:
                    network.remove()
                except Exception as e:
                    logger.warning("Failed to remove network %s: %s", network.id, e)
        except Exception as e:
            logger.debug("Error listing run networks: %s", e)

    @classmethod
    def reap_all_orphans(cls) -> int:
        """Reap all containers labeled with reporunner."""
        client = get_docker_client()
        count = 0
        try:
            containers = client.containers.list(all=True, filters={"label": "reporunner.run_id"})
            for container in containers:
                try:
                    container.stop(timeout=1)
                    container.remove(force=True, v=True)
                    count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return count
