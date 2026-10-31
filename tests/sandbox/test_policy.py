"""Unit tests for command policy."""

import pytest
from backend.sandbox.exec.policy import check_command_policy


@pytest.mark.parametrize("cmd", [
    "sudo apt update",
    "sudo su",
    "docker ps",
    "docker run alpine",
    "dockerd &",
    "mount /dev/sda1 /mnt",
    "umount /mnt",
    "nsenter -t 1 -m -u -i -n -p",
    "chroot /host",
    "iptables -L",
    "echo hello; sudo ls",
    "ls && docker images",
    "rm -rf /",
    "cat /var/run/docker.sock",
])
def test_forbidden_commands_blocked(cmd: str):
    allowed, reason = check_command_policy(cmd)
    assert not allowed
    assert reason is not None


@pytest.mark.parametrize("cmd", [
    "python3 -m pip install -r requirements.txt",
    "pytest -x -q",
    "npm test",
    "npm run build",
    "python3 app.py",
    "uvicorn main:app --host 0.0.0.0 --port 8000",
    "cat README.md",
    "ls -la /workspace",
    "git status",
])
def test_allowed_commands_permitted(cmd: str):
    allowed, reason = check_command_policy(cmd)
    assert allowed
    assert reason is None
