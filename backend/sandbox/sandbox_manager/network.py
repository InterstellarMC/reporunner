"""
Docker network isolation, modes, and transition lifecycle.

==============================================================================
SECURITY ARCHITECTURE & REMAINING NETWORK LIMITATIONS:
==============================================================================
1. Outbound Access During Cloning and Dependency Installation:
   - Untrusted repository setup requires outbound HTTPS access (TCP 443 / 80)
     to clone source code from git remotes and retrieve dependencies from
     package registries (PyPI, npm).
   - Therefore, complete outbound egress cannot be disabled during the initial
     clone and installation phases without breaking package resolution.

2. Intended Transition to Restricted Networking During Verification:
   - Once installation completes, the runner transitions containers from the
     external bridge network to an internal Docker network via `isolate_container()`.
   - The internal network (`internal=True`) disables the default gateway,
     cutting off all outbound internet access during independent verification.

3. Literal-IP SSRF Exposure & Lack of Full Egress / SSRF Protection:
   - While `METADATA_BLACKHOLE_HOSTS` overrides hostname resolution via `/etc/hosts`
     (e.g. `metadata.google.internal` -> `127.0.0.1`), POSIX network resolution
     treats literal IP strings (such as `http://169.254.169.254/` or private RFC 1918
     IP addresses) as already numeric. Literal IP lookups bypass `/etc/hosts`.
   - Consequently, Docker `extra_hosts` DOES NOT prevent raw TCP connections to literal
     cloud metadata IP addresses (169.254.169.254) or internal intranet IPs if reachable.
   - Complete SSRF protection cannot be claimed solely through Docker container settings.

4. Host-Level Firewall Requirements:
   - To fully block literal IP metadata access and restrict RFC 1918 intranet egress
     during the initial setup phase, the host running the Docker daemon must enforce
     host-level firewall rules (e.g. iptables / nftables dropping outbound packets to
     169.254.169.254 and private subnets from Docker bridge interfaces) or enforce
     cloud metadata hop limits (e.g. IMDSv2 with hop-limit=1).
==============================================================================
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional
import docker
from docker.models.containers import Container
from docker.models.networks import Network
from backend.sandbox.sandbox_manager.docker_client import get_docker_client

logger = logging.getLogger(__name__)

# Cloud metadata hostname endpoints blackholed via /etc/hosts (hostname resolution only)
METADATA_BLACKHOLE_HOSTS: Dict[str, str] = {
    "metadata.google.internal": "127.0.0.1",
    "metadata.goog": "127.0.0.1",
    "instance-data": "127.0.0.1",
    "169.254.169.254": "127.0.0.1",
}


class NetworkManager:
    """Manages Docker networks for isolated sandbox execution with fail-closed security."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.client = get_docker_client()
        self.network_name = f"rr_net_{run_id}"
        self.internal_network_name = f"rr_net_internal_{run_id}"

    def ensure_network(self, internal: bool = False) -> Network:
        """
        Create or reuse a network only after validating its security configuration.
        Never trust an existing network based on its name alone.
        """
        target_name = self.internal_network_name if internal else self.network_name
        try:
            existing = self.client.networks.list(names=[target_name])
            if existing:
                network = existing[0]
                network.reload()
                attrs = network.attrs or {}
                driver = attrs.get("Driver")
                is_internal = attrs.get("Internal")
                labels = attrs.get("Labels") or {}
                options = attrs.get("Options") or {}
                icc = options.get("com.docker.network.bridge.enable_icc")

                problems = []
                if driver != "bridge":
                    problems.append(f"driver must be bridge, got {driver!r}")
                if is_internal is not internal:
                    problems.append(
                        f"internal mode must be {internal}, got {is_internal!r}"
                    )
                if labels.get("reporunner.run_id") != self.run_id:
                    problems.append("run_id label does not match")
                if icc != "false":
                    problems.append(f"ICC must be false, got {icc!r}")

                if problems:
                    raise RuntimeError(
                        f"Unsafe existing Docker network {target_name}: "
                        + "; ".join(problems)
                    )
                return network

            return self.client.networks.create(
                name=target_name,
                driver="bridge",
                internal=internal,
                options={"com.docker.network.bridge.enable_icc": "false"},
                labels={"reporunner.run_id": self.run_id},
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(
                "Failed to create or validate isolated network %s: %s. Failing closed.",
                target_name,
                e,
            )
            raise RuntimeError(
                f"Sandbox network isolation creation failed: {e}. "
                "Refusing insecure fallback to bridge."
            ) from e

    def isolate_container(self, container: Container) -> None:
        """
        Switch the container to its internal network and remove external access.
        Verify that only the internal network remains attached.
        """
        int_net = self.ensure_network(internal=True)

        # 1. Connect to internal network first if not already connected
        try:
            container.reload()
            curr_nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            if self.internal_network_name not in curr_nets:
                int_net.connect(container)
        except Exception as e:
            logger.error("Failed to connect container to internal network %s: %s", self.internal_network_name, e)
            raise RuntimeError(f"Container network isolation failed (connect to internal): {e}") from e

        # 2. Disconnect from ALL other attached networks
        container.reload()
        attached_nets = list(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
        disconnect_errors: List[str] = []

        for net_name in attached_nets:
            if net_name == self.internal_network_name:
                continue
            try:
                nets = self.client.networks.list(names=[net_name])
                if nets:
                    nets[0].disconnect(container)
                else:
                    self.client.api.disconnect_container_from_network(container.id, net_name)
            except Exception as e:
                logger.error("Failed to disconnect container from network %s: %s", net_name, e)
                disconnect_errors.append(f"{net_name}: {e}")

        if disconnect_errors:
            err_msg = "; ".join(disconnect_errors)
            raise RuntimeError(f"Container network isolation failed (disconnect external): {err_msg}")

        # 3. Verify final state: only internal network must be attached
        container.reload()
        final_nets = list(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
        if self.internal_network_name not in final_nets or len(final_nets) != 1:
            raise RuntimeError(
                f"Container network isolation verification failed: unexpected networks attached: {final_nets}"
            )

    def cleanup(self) -> None:
        """
        Remove all networks created for this run.
        Handles partially completed transitions by disconnecting any remaining containers first.
        """
        for name in (self.network_name, self.internal_network_name):
            try:
                networks = self.client.networks.list(names=[name])
                for net in networks:
                    # Force disconnect any remaining containers if attached
                    try:
                        net.reload()
                        containers = net.attrs.get("Containers", {})
                        for cont_id in containers.keys():
                            try:
                                net.disconnect(cont_id, force=True)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    net.remove()
            except Exception as e:
                logger.debug("Network cleanup exception for %s: %s", name, e)
