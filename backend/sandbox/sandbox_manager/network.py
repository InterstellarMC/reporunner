"""Docker network isolation and modes."""

from __future__ import annotations

import logging
from typing import Optional
import docker
from docker.models.networks import Network
from backend.sandbox.sandbox_manager.docker_client import get_docker_client

logger = logging.getLogger(__name__)


class NetworkManager:
    """Manages Docker networks for isolated sandbox execution."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.client = get_docker_client()
        self.network_name = f"rr_net_{run_id}"

    def ensure_network(self, internal: bool = False) -> Network:
        """Create or get network with run_id label."""
        try:
            existing = self.client.networks.list(names=[self.network_name])
            if existing:
                return existing[0]

            return self.client.networks.create(
                name=self.network_name,
                driver="bridge",
                internal=internal,
                labels={"reporunner.run_id": self.run_id},
            )
        except Exception as e:
            logger.warning("Could not create custom network %s: %s", self.network_name, e)
            # Fallback to bridge
            return self.client.networks.get("bridge")

    def cleanup(self) -> None:
        """Remove network if created."""
        try:
            networks = self.client.networks.list(names=[self.network_name])
            for net in networks:
                net.remove()
        except Exception as e:
            logger.debug("Network cleanup exception for %s: %s", self.network_name, e)
