"""Security hardening and reproducibility audit regression tests."""

import pytest
from unittest.mock import MagicMock, patch
import docker

from backend.sandbox.api.schemas import (
    CheckVerdict,
    CommandRequest,
    OverallVerdict,
    PatchProposal,
    PatchStatus,
    RunLimits,
    VerificationReport,
)
from backend.sandbox.facade import ExecutionService
from backend.sandbox.sandbox_manager.manager import SandboxManager, validate_mount_targets
from backend.sandbox.sandbox_manager.network import (
    NetworkManager,
    METADATA_BLACKHOLE_HOSTS,
    RUNTIME_NETWORK_SECURITY_POSTURE,
    get_host_firewall_recommendations,
)
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


# ============================================================================
# 7. Metadata SSRF and Host Firewall Policy Audit (Priority 1)
# ============================================================================

def test_metadata_blackhole_hosts_configuration_coverage():
    """
    Verify configuration: ensure metadata hostnames are mapped to 127.0.0.1 in extra_hosts.
    Note: This configures hostname resolution (/etc/hosts) only, NOT literal-IP routing.
    """
    required_hosts = [
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "169.254.169.254",
        "169.254.169.253",
        "instance-data.ec2.internal",
        "host.docker.internal",
        "metadata",
    ]
    for host in required_hosts:
        assert host in METADATA_BLACKHOLE_HOSTS, f"Missing {host} in blackhole list"
        assert METADATA_BLACKHOLE_HOSTS[host] == "127.0.0.1"


def test_runtime_network_security_posture_marks_literal_ip_as_unsupported_without_host_rules():
    """Verify runtime posture honestly reflects that literal-IP SSRF requires host-level firewall."""
    assert RUNTIME_NETWORK_SECURITY_POSTURE["inter_container_communication_blocked"] is True
    assert RUNTIME_NETWORK_SECURITY_POSTURE["metadata_hostnames_blackholed_in_etc_hosts"] is True
    assert RUNTIME_NETWORK_SECURITY_POSTURE["literal_ip_metadata_blocked_in_docker"] is False
    assert RUNTIME_NETWORK_SECURITY_POSTURE["private_network_rfc1918_blocked_in_docker"] is False
    assert RUNTIME_NETWORK_SECURITY_POSTURE["verification_mode_egress_blocked"] is True


def test_host_firewall_recommendations_coverage():
    """Verify host-level firewall recommendations document required DOCKER-USER rules."""
    rules = get_host_firewall_recommendations()
    assert any("169.254.169.254" in r for r in rules)
    assert any("169.254.0.0/16" in r for r in rules)
    assert any("DOCKER-USER" in r for r in rules)


# ============================================================================
# 8. Verification Lifecycle & State Machine Hardening (Priority 2)
# ============================================================================

def test_cannot_provision_run_twice():
    """Calling provision_run on an already provisioned/active run must fail closed."""
    service = ExecutionService(run_id="test_reprovision")
    service.state = "ACTIVE"
    with pytest.raises(RuntimeError, match="already in ACTIVE state"):
        service.provision_run()


def test_provision_run_clone_failure_fails_closed_and_cleans_up():
    """Repository clone failure must set state to FAILED, clean up sandbox, and preserve error."""
    service = ExecutionService(run_id="test_prov_clone_fail")
    assert service.state == "CREATED"

    with patch.object(service.sandbox, "start") as mock_start:
        with patch.object(service.workspace, "clone_repository", return_value=(False, "Git clone network timeout")):
            with patch.object(service.sandbox, "stop") as mock_stop:
                with pytest.raises(RuntimeError, match="Git clone network timeout"):
                    service.provision_run(repo_url="https://github.com/org/repo.git")

                assert service.state == "FAILED"
                mock_start.assert_called_once()
                mock_stop.assert_called_once()


def test_provision_run_snapshot_failure_fails_closed_and_cleans_up():
    """Snapshot initialization failure must set state to FAILED, clean up sandbox, and preserve error."""
    service = ExecutionService(run_id="test_prov_snap_fail")
    assert service.state == "CREATED"

    with patch.object(service.sandbox, "start") as mock_start:
        with patch.object(service.patch_applier, "_save_shadow_snapshot", side_effect=OSError("Shadow snapshot disk full")):
            with patch.object(service.sandbox, "stop") as mock_stop:
                with pytest.raises(OSError, match="Shadow snapshot disk full"):
                    service.provision_run()  # No repo_url triggers initial shadow snapshot path

                assert service.state == "FAILED"
                mock_start.assert_called_once()
                mock_stop.assert_called_once()


def test_provision_run_cleanup_failure_does_not_mask_original_error():
    """If sandbox cleanup raises after a provisioning failure, the original failure is preserved."""
    service = ExecutionService(run_id="test_prov_cleanup_mask")
    assert service.state == "CREATED"

    with patch.object(service.sandbox, "start"):
        with patch.object(service.workspace, "clone_repository", return_value=(False, "Original clone error")):
            with patch.object(service.sandbox, "stop", side_effect=RuntimeError("Cleanup daemon failure")):
                # Original clone error must be raised, NOT the cleanup error
                with pytest.raises(RuntimeError, match="Original clone error"):
                    service.provision_run(repo_url="https://github.com/org/repo.git")

                assert service.state == "FAILED"


def test_verification_cannot_start_on_stopped_sandbox():
    """Verification must fail if the sandbox container is not running."""
    service = ExecutionService(run_id="test_verify_stopped")
    service.state = "ACTIVE"
    service.sandbox.is_running = False
    with pytest.raises(RuntimeError, match="Cannot verify: sandbox is not running"):
        service.verify()


def test_verification_cannot_be_restarted_after_completion():
    """A completed or verifying run cannot be verified again."""
    service = ExecutionService(run_id="test_double_verify")
    service.state = "VERIFIED"
    service.sandbox.is_running = True
    service.sandbox.container = MagicMock()
    with pytest.raises(RuntimeError, match="Verification is not permitted while run is VERIFIED"):
        service.verify()


def test_verification_isolation_failure_stops_sandbox_and_sets_failed():
    """If network isolation fails during verify, sandbox must be stopped and state set to FAILED."""
    service = ExecutionService(run_id="test_verify_iso_fail")
    service.state = "ACTIVE"
    service.sandbox.is_running = True
    service.sandbox.container = MagicMock()

    with patch.object(
        service.sandbox.network_manager,
        "isolate_container",
        side_effect=RuntimeError("Docker isolation network disconnect failed"),
    ):
        with patch.object(service.sandbox, "stop") as mock_stop:
            with pytest.raises(RuntimeError, match="Verification aborted because network isolation failed"):
                service.verify()

            assert service.state == "FAILED"
            mock_stop.assert_called_once()


def test_ordinary_commands_and_patches_rejected_after_verification_starts():
    """Commands, patches, rollbacks, and background processes must be rejected during/after verification."""
    service = ExecutionService(run_id="test_reject_after_verify")
    service.state = "VERIFYING"

    # 1. run_command rejected
    cmd_req = CommandRequest(
        run_id="test_reject_after_verify",
        command_id="cmd_post_verify",
        command="echo forbidden",
    )
    with pytest.raises(RuntimeError, match="Command execution is not permitted while run is VERIFYING"):
        service.run_command(cmd_req)

    # 2. validate_patch rejected
    patch_prop = PatchProposal(
        patch_id="p1",
        run_id="test_reject_after_verify",
        base_snapshot_id="snap1",
        origin="orchestrator",
    )
    val_res = service.validate_patch(patch_prop)
    assert val_res is not None
    assert val_res.status == PatchStatus.REJECTED

    # 3. apply_patch rejected
    apply_res = service.apply_patch(patch_prop)
    assert apply_res.status == PatchStatus.REJECTED

    # 4. rollback rejected
    rb_res = service.rollback("snap1")
    assert rb_res.status == "FAILED"

    # 5. start_process rejected
    started, msg = service.start_process("proc1", "python3 -m http.server")
    assert started is False
    assert "Cannot start a background process while run is VERIFYING" in msg


def test_fresh_reproducibility_verification_lifecycle():
    """Fresh container started directly in CREATED state can verify and reaches VERIFIED state."""
    service = ExecutionService(run_id="test_fresh_repro_lifecycle", mount_workspace_volume=False)
    assert service.state == "CREATED"

    # Mock container running
    service.sandbox.is_running = True
    service.sandbox.container = MagicMock()

    mock_report = VerificationReport(
        run_id="test_fresh_repro_lifecycle",
        overall_status=CheckVerdict.PASS,
        completion_level=OverallVerdict.COMPLETE,
        is_green=True,
        checks=[],
    )

    with patch.object(service.sandbox.network_manager, "isolate_container") as mock_iso:
        with patch("backend.verifier.engine.VerificationEngine.verify", return_value=mock_report):
            report = service.verify()
            assert report.overall_status == CheckVerdict.PASS
            assert service.state == "VERIFIED"
            mock_iso.assert_called_once_with(service.sandbox.container)


# ============================================================================
# 9. Loopback and Application Verification Compatibility (Priority 3)
# ============================================================================

def test_loopback_preserved_in_real_isolated_docker():
    """Prove that 127.0.0.1 (loopback) remains accessible after network isolation."""
    sandbox = SandboxManager(run_id="test_loopback_iso", base_image="reporunner-python:3.11-git1")
    try:
        cont = sandbox.start()
        # Perform network isolation
        sandbox.network_manager.isolate_container(cont)

        # Execute loopback HTTP server and client test entirely inside isolated container
        py_test_cmd = (
            "python3 -c \""
            "import http.server, threading, urllib.request; "
            "server = http.server.HTTPServer(('127.0.0.1', 9988), http.server.SimpleHTTPRequestHandler); "
            "t = threading.Thread(target=server.serve_forever, daemon=True); "
            "t.start(); "
            "resp = urllib.request.urlopen('http://127.0.0.1:9988', timeout=5); "
            "assert resp.status == 200; "
            "server.shutdown(); "
            "print('LOOPBACK_HTTP_SUCCESS')\""
        )
        code, out, _ = sandbox.exec_in_sandbox(py_test_cmd, timeout_s=15)
        assert code == 0, f"Loopback check failed in isolated container: {out.decode('utf-8')}"
        assert b"LOOPBACK_HTTP_SUCCESS" in out
    finally:
        sandbox.stop()

# ============================================================================
# 10. Resource and Container Hardening Audit (Priority 4)
# ============================================================================

def test_read_only_root_configures_tmpfs():
    """Verify that read_only_root enables read_only flag and writable tmpfs for /tmp."""
    limits = RunLimits(memory_limit="256m", cpu_limit=1.0, pids_limit=100)
    cfg = get_container_host_config(limits, read_only_root=True)
    assert cfg.get("read_only") is True
    assert "tmpfs" in cfg
    assert "/tmp" in cfg["tmpfs"]
    assert "rw" in cfg["tmpfs"]["/tmp"]


def test_container_host_config_security_options():
    """Verify host configuration drops ALL capabilities, enables no-new-privileges, and locks swap."""
    limits = RunLimits(memory_limit="512m", cpu_limit=2.0, pids_limit=200)
    cfg = get_container_host_config(limits)
    assert cfg["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in cfg["security_opt"]
    assert cfg["memswap_limit"] == cfg["mem_limit"]  # Swap disabled/equal to mem
    assert cfg["nano_cpus"] == 2_000_000_000


def test_production_mount_validation_accepts_approved_mount():
    """Prove production validate_mount_targets accepts approved /workspace volume mount."""
    valid_vols = {"rr_vol_123": {"bind": "/workspace", "mode": "rw"}}
    validate_mount_targets(valid_vols)  # Must not raise


@pytest.mark.parametrize("forbidden_bind", [
    "/var/run/docker.sock",
    "/etc",
    "/root",
    "/host",
    "/dev",
])
def test_production_mount_validation_rejects_unapproved_mounts(forbidden_bind):
    """Prove production validate_mount_targets strictly rejects unapproved mounts."""
    invalid_vols = {"bad_vol": {"bind": forbidden_bind, "mode": "rw"}}
    with pytest.raises(PermissionError, match="Forbidden mount path"):
        validate_mount_targets(invalid_vols)


def test_sandbox_start_rejects_unapproved_mount_before_container_creation():
    """Prove unapproved mount rejection occurs before Docker creates or starts a container."""
    sandbox = SandboxManager(run_id="test_mount_rejection_order")
    # Patch volume creation to inject a forbidden bind target into volumes
    with patch.object(sandbox.client.volumes, "create", return_value=MagicMock()):
        with patch("backend.sandbox.sandbox_manager.manager.DEFAULT_WORKSPACE_DIR", "/forbidden/host/path"):
            with patch.object(sandbox.client.containers, "create") as mock_cont_create:
                with pytest.raises(PermissionError, match="Forbidden mount path"):
                    sandbox.start()
                # Crucial security invariant: container create must NEVER be called
                mock_cont_create.assert_not_called()


def test_read_only_root_runtime_enforcement():
    """
    Real Docker runtime test proving read_only_root enforces:
    - Root filesystem (/) is read-only.
    - /tmp is writable tmpfs.
    - /tmp is mounted with noexec (scripts in /tmp cannot execute).
    - /workspace volume is writable.
    - Standard commands and Python execution succeed in /workspace.
    """
    sandbox = SandboxManager(
        run_id="test_ro_runtime",
        base_image="reporunner-python:3.11-git1",
        read_only_root=True,
    )
    try:
        sandbox.start()

        # 1. Root filesystem (/) is strictly read-only
        code_root, out_root, _ = sandbox.exec_in_sandbox(["touch", "/test_root_file"], user="0")
        assert code_root != 0, "Writing to root filesystem unexpectedly succeeded on read-only root"
        assert b"Read-only file system" in out_root

        # 2. /tmp is writable
        code_tmp_w, _, _ = sandbox.exec_in_sandbox(["touch", "/tmp/test_tmp_file"])
        assert code_tmp_w == 0, "/tmp was not writable"

        # 3. /tmp is mounted with noexec (executing script in /tmp must be denied)
        sandbox.exec_in_sandbox("echo '#!/bin/sh\necho NOEXEC_FAILED' > /tmp/bad.sh && chmod +x /tmp/bad.sh")
        code_noexec, out_noexec, _ = sandbox.exec_in_sandbox(["/tmp/bad.sh"])
        assert code_noexec != 0, "Execution of script in /tmp unexpectedly succeeded on noexec mount"
        assert b"Permission denied" in out_noexec or code_noexec == 126

        # 4. /workspace volume is writable
        code_ws_w, _, _ = sandbox.exec_in_sandbox(["touch", "/workspace/test_ws_file"])
        assert code_ws_w == 0, "/workspace was not writable"

        # 5. Commands and Python execution work properly in /workspace
        code_py, out_py, _ = sandbox.exec_in_sandbox(
            ["python3", "-c", "print('RUNNER_WORKSPACE_OK')"],
            workdir="/workspace",
        )
        assert code_py == 0
        assert b"RUNNER_WORKSPACE_OK" in out_py

    finally:
        sandbox.stop()
