# RepoRunner Base Sandbox Images

These images provide pinned runtimes (Python 3.11, Node 20) with Git and system certificates pre-installed, avoiding runtime `apt-get` calls in capability-restricted sandbox containers.

## Reproducible Build Commands

### Python 3.11 Base Image
```bash
docker build -t reporunner-python:3.11-git1 backend/sandbox/images/python
```

### Node 20 Base Image
```bash
docker build -t reporunner-node:20-git1 backend/sandbox/images/node
```

## Security Profiles
- Non-root user: `runner` (`UID 10001:10001`)
- Capabilities: `ALL` dropped (`--cap-drop=ALL`)
- Security options: `no-new-privileges:true`
- Narrow Git config: `safe.directory /workspace` and `safe.directory /tmp/reporunner-repository-clone` (no wildcard '*').

## Network Architecture & Known Limitations
1. **Outbound Egress During Setup**: Outbound HTTPS (ports 443/80) is permitted during repository cloning and dependency resolution (`pip`, `npm`).
2. **Transition to Internal Mode**: Post-installation verification transitions containers to an internal Docker bridge network (`internal=True`) with no default gateway, cutting off internet egress.
3. **Literal-IP SSRF Exposure**: Docker `extra_hosts` blackholes standard cloud metadata hostnames (`metadata.google.internal`), but POSIX network resolution connects directly to raw IP literals (`http://169.254.169.254/`). Full literal-IP SSRF protection requires host-level iptables/firewall rules or cloud provider IMDS hop-limits.
