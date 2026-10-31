"""Security hardening and reproducibility audit regression tests."""

import pytest
from unittest.mock import MagicMock, patch
import docker

from backend.sandbox.api.schemas import RunLimits
from backend.sandbox.sandbox_manager.manager import SandboxManager
from backend.sandbox.sandbox_manager.network import NetworkManager, METADATA_BLACKHOLE_HOSTS
from backend.sandbox.sandbox_manager.profiles import (
    get_container_host_config,
    parse_memory_limit,
    get_base_image_build_command,
    REPORUNNER_BASE_IMAGE_PYTHON,
    REPORUNNER_BASE_IMAGE_NODE,
)
from backend.sandbox.workspace.manager import WorkspaceManager, validate_git_url, HEX_SHA_REGEX


from docker.models.networks import NetworkCollection


# ============================================================================
# 1. Network Creation & Connection Failures (Fail-Closed)
# ============================================================================

def test_network_creation_failure_fails_closed():
    """Verify that network creation failure raises RuntimeError and refuses fallback."""
    mgr = NetworkManager(run_id="test_net_fail")
    with patch.object(NetworkCollection, "list", return_value=[]):
        with patch.object(NetworkCollection, "create", side_effect=docker.errors.APIError("Network daemon error")):
            with pytest.raises(RuntimeError, match="Sandbox network isolation creation failed"):
                mgr.ensure_network()


def test_sandbox_start_fails_closed_on_network_failure():
    """Verify SandboxManager.start() fails closed when network creation fails."""
    sandbox = SandboxManager(run_id="test_start_net_fail", base_image="reporunner-python:3.11-git1")
    with patch.object(sandbox.network_manager, "ensure_network", side_effect=RuntimeError("Network allocation failed")):
        with pytest.raises(RuntimeError, match="Sandbox container startup failed during network provisioning"):
            sandbox.start()
        # Verify container was not left running
        assert sandbox.is_running is False
        assert sandbox.container is None


def test_sandbox_start_fails_closed_on_container_creation_failure():
    """Verify SandboxManager.start() cleans up resources if container creation fails."""
    sandbox = SandboxManager(run_id="test_start_cont_fail", base_image="reporunner-python:3.11-git1")
    with patch.object(sandbox.network_manager, "ensure_network", return_value=MagicMock(name="mock_net")):
        with patch.object(sandbox.client.containers, "create", side_effect=docker.errors.APIError("Resource exhaustion")):
            with pytest.raises(RuntimeError, match="Sandbox container creation/start failed"):
                sandbox.start()
            assert sandbox.is_running is False
            assert sandbox.container is None


def test_network_isolation_switching_unit():
    """Verify isolate_container switches container to an internal network and disconnects external."""
    net_mgr = NetworkManager(run_id="test_switch")
    mock_container = MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {
            "Networks": {
                net_mgr.network_name: {},
            }
        }
    }
    reload_count = 0
    def reload_side_effect():
        nonlocal reload_count
        reload_count += 1
        if reload_count == 1:
            mock_container.attrs["NetworkSettings"]["Networks"] = {net_mgr.network_name: {}}
        elif reload_count == 2:
            mock_container.attrs["NetworkSettings"]["Networks"] = {
                net_mgr.network_name: {},
                net_mgr.internal_network_name: {},
            }
        else:
            mock_container.attrs["NetworkSettings"]["Networks"] = {
                net_mgr.internal_network_name: {},
            }
    mock_container.reload.side_effect = reload_side_effect

    mock_ext_net = MagicMock()
    mock_ext_net.name = net_mgr.network_name
    mock_int_net = MagicMock()
    mock_int_net.name = net_mgr.internal_network_name

    with patch.object(net_mgr, "ensure_network", return_value=mock_int_net):
        with patch.object(NetworkCollection, "list", return_value=[mock_ext_net]):
            net_mgr.isolate_container(mock_container)
            mock_int_net.connect.assert_called_once_with(mock_container)
            mock_ext_net.disconnect.assert_called_once_with(mock_container)


def test_network_isolation_failure_to_disconnect_raises():
    """Verify that failure to disconnect an external network raises explicit RuntimeError."""
    net_mgr = NetworkManager(run_id="test_switch_fail_disc")
    mock_container = MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {
            "Networks": {
                net_mgr.network_name: {},
            }
        }
    }
    mock_ext_net = MagicMock()
    mock_ext_net.name = net_mgr.network_name
    mock_ext_net.disconnect.side_effect = docker.errors.APIError("Disconnect forbidden")
    mock_int_net = MagicMock()
    mock_int_net.name = net_mgr.internal_network_name

    with patch.object(net_mgr, "ensure_network", return_value=mock_int_net):
        with patch.object(NetworkCollection, "list", return_value=[mock_ext_net]):
            with pytest.raises(RuntimeError, match="Container network isolation failed \\(disconnect external\\)"):
                net_mgr.isolate_container(mock_container)


def test_network_isolation_switching_real_docker():
    """Real Docker test: verify network transition physically disconnects external and attaches internal."""
    sandbox = SandboxManager(run_id="test_real_iso", base_image="reporunner-python:3.11-git1")
    try:
        cont = sandbox.start()
        initial_nets = list(cont.attrs["NetworkSettings"]["Networks"].keys())
        assert sandbox.network_manager.network_name in initial_nets
        assert sandbox.network_manager.internal_network_name not in initial_nets

        # Perform isolation transition
        sandbox.network_manager.isolate_container(cont)

        # Verify post-transition state in live container
        cont.reload()
        final_nets = list(cont.attrs["NetworkSettings"]["Networks"].keys())
        assert sandbox.network_manager.network_name not in final_nets
        assert sandbox.network_manager.internal_network_name in final_nets
        assert len(final_nets) == 1
    finally:
        sandbox.stop()


def test_git_safe_directory_scoped_no_wildcard():
    """Verify that safe.directory has no global wildcard '*' and is scoped only to required paths."""
    sandbox = SandboxManager(run_id="test_safe_dir", base_image="reporunner-python:3.11-git1")
    try:
        sandbox.start()
        code, out, _ = sandbox.exec_in_sandbox(["git", "config", "--system", "--get-all", "safe.directory"])
        assert code == 0
        entries = [line.strip() for line in out.decode("utf-8").splitlines() if line.strip()]
        # Wildcard MUST NOT be present
        assert "*" not in entries
        # Scoped directories must be present
        assert "/workspace" in entries
        assert "/tmp/reporunner-repository-clone" in entries
    finally:
        sandbox.stop()


# ============================================================================
# 2. Workspace Permissions & Ownership (Least Privilege, No 777)
# ============================================================================

def test_workspace_permissions_and_no_chmod_777():
    """Verify /workspace and .shadow_snapshots are configured with least-privilege permissions."""
    sandbox = SandboxManager(run_id="test_perms_audit", base_image="reporunner-python:3.11-git1")
    try:
        sandbox.start()
        # Inspect permissions of /workspace
        code, out, _ = sandbox.exec_in_sandbox(["stat", "-c", "%a %u:%g", "/workspace"], user="10001:10001")
        assert code == 0
        perm_owner = out.decode("utf-8").strip()
        # Must be 755 (or 750), owned by 10001:10001; NEVER 777
        assert "777" not in perm_owner
        assert "10001:10001" in perm_owner

        # Inspect permissions of /workspace/.shadow_snapshots
        code, out, _ = sandbox.exec_in_sandbox(["stat", "-c", "%a %u:%g", "/workspace/.shadow_snapshots"], user="10001:10001")
        assert code == 0
        snap_perm = out.decode("utf-8").strip()
        assert snap_perm.startswith("700")
        assert "10001:10001" in snap_perm
    finally:
        sandbox.stop()


def test_temporary_clone_dir_cleaned_up_after_clone():
    """Verify /tmp/reporunner-repository-clone does not persist in the container."""
    sandbox = SandboxManager(run_id="test_temp_cleanup", base_image="reporunner-python:3.11-git1")
    try:
        sandbox.start()
        wm = WorkspaceManager(sandbox=sandbox)
        # Attempt clone with invalid SHA (triggers failure path)
        success, _ = wm.clone_repository(
            repo_url="https://github.com/benjaminp/six.git",
            commit_sha="0000000000000000000000000000000000000000",
        )
        assert success is False
        # Verify temp directory is completely removed
        code, _, _ = sandbox.exec_in_sandbox(["test", "-d", "/tmp/reporunner-repository-clone"])
        assert code != 0, "Temporary clone directory was not cleaned up after clone failure"
    finally:
        sandbox.stop()


# ============================================================================
# 3. Git Commit Verification & Ambiguity Protection
# ============================================================================

@pytest.mark.parametrize("invalid_sha", [
    "12345",              # Too short (< 7 hex chars)
    "g" * 40,             # Non-hex characters
    "0" * 41,             # Too long (> 40 hex chars)
    "; rm -rf /",         # Shell metacharacters
    "abc def",            # Whitespace
    "",                   # Empty
])
def test_invalid_commit_shas_rejected_early(invalid_sha):
    """Verify invalid commit SHAs are rejected before running git."""
    sandbox = MagicMock()
    wm = WorkspaceManager(sandbox=sandbox)
    success, err = wm.clone_repository(repo_url="https://github.com/org/repo.git", commit_sha=invalid_sha)
    assert success is False
    assert "not a valid hex SHA" in err
    sandbox.exec_in_sandbox.assert_not_called()


def test_exact_40_char_sha_mismatch_rejected():
    """Verify exact 40-char SHA mismatch fails even if prefix matched."""
    sandbox = SandboxManager(run_id="test_exact_sha", base_image="reporunner-python:3.11-git1")
    try:
        sandbox.start()
        wm = WorkspaceManager(sandbox=sandbox)
        # Mock git rev-parse returning a different SHA
        with patch.object(sandbox, "exec_in_sandbox") as mock_exec:
            # Check git version -> 0
            # Clone -> 0
            # Checkout -> 0
            # cp -> 0
            # rev-parse HEAD -> returns different 40-char SHA
            mock_exec.side_effect = [
                (0, b"git version 2.39.5\n", 0.01),
                (0, b"", 0.01),
                (0, b"", 0.01),
                (0, b"", 0.01),
                (0, b"", 0.01),
                (0, b"a" * 40 + b"\n", 0.01),  # actual HEAD
                (0, b"snap1234\n", 0.01),
                (0, b"", 0.01),
                (0, b"", 0.01),
            ]
            success, err = wm.clone_repository(
                repo_url="https://github.com/org/repo.git",
                commit_sha="b" * 40,
            )
            assert success is False
            assert "Pinned SHA mismatch" in err
    finally:
        sandbox.stop()


# ============================================================================
# 4. CPU & Memory Limits Validation and Enforcement
# ============================================================================

@pytest.mark.parametrize("invalid_mem", [
    "-1g",
    "0m",
    "0g",
    "32m",        # Below 64 MiB minimum
    "unlimited",
    "invalid",
    "",
])
def test_invalid_memory_limits_raise_value_error(invalid_mem):
    """Verify parse_memory_limit strictly rejects invalid or dangerous memory values."""
    with pytest.raises(ValueError):
        parse_memory_limit(invalid_mem)


@pytest.mark.parametrize("valid_mem,expected_bytes", [
    ("64m", 64 * 1024 * 1024),
    ("256m", 256 * 1024 * 1024),
    ("1g", 1 * 1024 * 1024 * 1024),
    ("2.5g", int(2.5 * 1024 * 1024 * 1024)),
])
def test_valid_memory_limits_parsed_correctly(valid_mem, expected_bytes):
    assert parse_memory_limit(valid_mem) == expected_bytes


@pytest.mark.parametrize("invalid_cpu", [0.0, -1.0, -0.5])
def test_invalid_cpu_limits_raise_value_error(invalid_cpu):
    limits = RunLimits(cpu_limit=invalid_cpu)
    with pytest.raises(ValueError, match="cpu_limit must be positive"):
        get_container_host_config(limits)


@pytest.mark.parametrize("invalid_pid", [0, -1, -100])
def test_invalid_pid_limits_raise_value_error(invalid_pid):
    limits = RunLimits(pids_limit=invalid_pid)
    with pytest.raises(ValueError, match="pids_limit must be positive"):
        get_container_host_config(limits)


# ============================================================================
# 5. Base Image Configuration & Reproducible Build Commands
# ============================================================================

def test_base_image_configuration_and_build_commands():
    """Verify reproducible build commands are defined for all base images."""
    cmd_py = get_base_image_build_command(REPORUNNER_BASE_IMAGE_PYTHON)
    assert cmd_py is not None
    assert "docker build -t reporunner-python:3.11-git1" in cmd_py

    cmd_node = get_base_image_build_command(REPORUNNER_BASE_IMAGE_NODE)
    assert cmd_node is not None
    assert "docker build -t reporunner-node:20-git1" in cmd_node

    assert REPORUNNER_BASE_IMAGE_PYTHON == "reporunner-python:3.11-git1"
    assert REPORUNNER_BASE_IMAGE_NODE == "reporunner-node:20-git1"


# ============================================================================
# 6. Metadata Blackhole Protection
# ============================================================================

def test_metadata_blackhole_hosts_defined():
    """Verify SSRF cloud metadata hostnames are in the blackhole list."""
    assert "metadata.google.internal" in METADATA_BLACKHOLE_HOSTS
    assert "169.254.169.254" in METADATA_BLACKHOLE_HOSTS
    assert METADATA_BLACKHOLE_HOSTS["metadata.google.internal"] == "127.0.0.1"






def test_sandbox_initialization_failure_fails_closed():
    """Initialization failure must stop the container and prevent activation."""
    sandbox = SandboxManager(
        run_id="test_init_fail_closed",
        base_image="reporunner-python:3.11-git1",
    )

    mock_network = MagicMock()
    mock_network.name = "mock_network"

    mock_container = MagicMock()
    mock_container.exec_run.return_value = (1, b"permission denied")

    with patch.object(
        sandbox.network_manager,
        "ensure_network",
        return_value=mock_network,
    ):
        with patch(
            "docker.models.containers.ContainerCollection.create",
            return_value=mock_container,
        ):
            with patch(
                "backend.sandbox.sandbox_manager.manager.Reaper.reap_run"
            ) as mock_reaper:
                with patch.object(
                    sandbox.network_manager,
                    "cleanup",
                ) as mock_cleanup:
                    with pytest.raises(
                        RuntimeError,
                        match="Sandbox initialization failed",
                    ):
                        sandbox.start()

    assert sandbox.is_running is False
    assert sandbox.container is None
    mock_container.start.assert_called_once()
    mock_container.exec_run.assert_called_once()
    assert mock_reaper.call_count == 2
    mock_cleanup.assert_called_once()


@pytest.mark.parametrize(
    "internal,overrides",
    [
        (False, {"Driver": "host"}),
        (False, {"Internal": True}),
        (False, {"Labels": {"reporunner.run_id": "another_run"}}),
        (False, {"Labels": {}}),
        (
            False,
            {
                "Options": {
                    "com.docker.network.bridge.enable_icc": "true"
                }
            },
        ),
        (True, {"Internal": False}),
    ],
)
def test_existing_unsafe_network_is_rejected(internal, overrides):
    """Never reuse an existing Docker network with unsafe configuration."""
    mgr = NetworkManager(run_id="test_network_validation")

    attrs = {
        "Driver": "bridge",
        "Internal": internal,
        "Labels": {"reporunner.run_id": "test_network_validation"},
        "Options": {
            "com.docker.network.bridge.enable_icc": "false"
        },
    }
    attrs.update(overrides)

    existing_network = MagicMock()
    existing_network.attrs = attrs

    with patch.object(
        NetworkCollection,
        "list",
        return_value=[existing_network],
    ):
        with pytest.raises(
            RuntimeError,
            match="Unsafe existing Docker network",
        ):
            mgr.ensure_network(internal=internal)


@pytest.mark.parametrize("internal", [False, True])
def test_existing_secure_network_is_reused(internal):
    """Reuse an existing network only when all security settings match."""
    mgr = NetworkManager(run_id="test_network_validation")

    expected_name = (
        mgr.internal_network_name if internal else mgr.network_name
    )

    existing_network = MagicMock()
    existing_network.name = expected_name
    existing_network.attrs = {
        "Driver": "bridge",
        "Internal": internal,
        "Labels": {"reporunner.run_id": "test_network_validation"},
        "Options": {
            "com.docker.network.bridge.enable_icc": "false"
        },
    }

    with patch.object(
        NetworkCollection,
        "list",
        return_value=[existing_network],
    ):
        result = mgr.ensure_network(internal=internal)

    assert result is existing_network
