import asyncio
import json
import os
import tarfile
import tempfile
from pathlib import Path
from .process import Output, run
from .workspace import Workspace, source_files
from .limits import SandboxLimits, MIB
from .runtimes import RUNTIMES


class DockerError(RuntimeError):
    pass


class Docker:
    def __init__(self, python_image=None, node_image=None):
        self.images = {"python": python_image or os.getenv("REPORUNNER_PYTHON_IMAGE", "python:3.11-slim"),
                       "node": node_image or os.getenv("REPORUNNER_NODE_IMAGE", "node:20-slim")}
        self.images.update({stack: os.getenv("REPORUNNER_" + stack.upper() + "_IMAGE", value[0]) for stack, value in RUNTIMES.items() if stack not in self.images})
        self.checked = set()
        self.limits = SandboxLimits.from_env()

    async def health(self):
        try:
            result = await run(["docker", "version", "--format", "{{.Server.Version}}"], 5)
            return {"available": result.code == 0, "version": result.stdout.strip(),
                    "reason": "" if result.code == 0 else "Docker Desktop is not ready"}
        except (OSError, asyncio.TimeoutError):
            return {"available": False, "version": "", "reason": "Start Docker Desktop to execute repositories"}

    async def ensure_image(self, stack):
        image = self.images[stack]
        inspect = await run(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], 10)
        if inspect.code:
            pulled = await run(["docker", "pull", image], 180)
            if pulled.code:
                raise DockerError("could not fetch runtime image: " + pulled.stderr[-1000:])
            inspect = await run(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], 10)
        digests = json.loads(inspect.stdout or "null")
        pinned = digests[0] if digests else image
        if pinned not in self.checked:
            argv = RUNTIMES[stack][1]
            sanity = await run(["docker", "run", "--rm", *self.restrictions(False), "--env", "HOME=/tmp", "--env", "DOTNET_CLI_HOME=/tmp", pinned, *argv], 30)
            if sanity.code:
                raise DockerError("cached runtime image failed its standard-library sanity check; choose another official runtime tag")
            self.checked.add(pinned)
        return pinned

    def restrictions(self, network=False):
        return ["--network", "bridge" if network else "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
                "--memory", f"{self.limits.memory_mb}m", "--cpus", "1", "--read-only",
                "--tmpfs", f"/tmp:rw,exec,nosuid,size={self.limits.temporary_mb}m,mode=1777"]

    async def create(self, workspace: Workspace, stack):
        image = await self.ensure_image(stack)
        name = "rr-" + workspace.run_id[:24]
        result = await run(["docker", "run", "-d", "--name", name, "--label", "reporunner=true",
            "--label", "reporunner.run=" + workspace.run_id, *self.restrictions(workspace.network),
            "--tmpfs", f"/workspace:rw,exec,nosuid,size={self.limits.workspace_mb}m,mode=1777",
            "--user", "65534:65534", "--workdir", "/workspace", "--env", "HOME=/tmp",
            "--env", "PORT=8000",
            "--env", "XDG_RUNTIME_DIR=/tmp", "--env", "GOPATH=/workspace/.rr-go",
            "--env", "MAVEN_CONFIG=/tmp/maven-config",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONPATH=/tmp/rr-deps:/workspace",
            "--env", "npm_config_cache=/tmp/npm-cache", "--env", "CI=true",
            "--env", "GOCACHE=/tmp/go-cache", "--env", "GOMODCACHE=/workspace/.rr-go-mod", "--env", "CARGO_HOME=/workspace/.rr-cargo",
            "--env", "DOTNET_CLI_HOME=/tmp", "--env", "DOTNET_CLI_TELEMETRY_OPTOUT=1", "--env", "NUGET_PACKAGES=/workspace/.rr-nuget",
            image, "sleep", "3660"], 15)
        if result.code:
            raise DockerError(result.stderr[-2000:])
        workspace.container, workspace.image = name, image
        try:
            await self.sync(workspace)
        except BaseException:
            await self.remove(name)
            workspace.container = None
            raise

    async def sync(self, workspace):
        # Spool tar contents to disk rather than making a second full-size RAM copy.
        with tempfile.SpooledTemporaryFile(max_size=8 * MIB) as data:
            with tarfile.open(fileobj=data, mode="w") as tar:
                for p in source_files(workspace.path):
                    info = tar.gettarinfo(str(p), arcname=p.relative_to(workspace.path).as_posix())
                    info.uid = info.gid = 65534
                    info.uname = info.gname = "nobody"
                    info.mode = 0o644
                    with p.open("rb") as content:
                        tar.addfile(info, content)
            data.seek(0)
            result = await run(["docker", "exec", "-i", workspace.container,
                "tar", "--no-same-owner", "-xf", "-", "-C", "/workspace"], 60, data)
        if result.code:
            raise DockerError("workspace copy failed: " + result.stderr[-1000:])

    async def execute(self, workspace, argv, cwd=".", timeout=60):
        if not workspace.container:
            raise DockerError("sandbox is not running")
        if not argv or argv[0] not in {"python", "python3", "pip", "pip3", "node", "npm", "npx", "go", "cargo", "rustc", "mvn", "java", "javac", "dotnet", "ls", "cat", "pwd"}:
            return Output(126, "", "Command is outside the supported runtime tool policy")
        return await run(["docker", "exec", "--workdir", "/workspace" + ("/" + cwd if cwd != "." else ""),
            *[part for name in workspace.resources for part in ("--env", name)],
            workspace.container, "timeout", "--signal=KILL", f"{max(1, timeout):.1f}s", *argv], timeout + 3, extra_env=workspace.resources)

    async def remove(self, name):
        if name:
            await run(["docker", "rm", "-f", name], 10)

    async def build(self, workspace, dockerfile, tag, timeout):
        return await run(["docker", "build", "--network=" + ("default" if workspace.network else "none"),
            "--progress=plain", "--tag", tag, "--file", str(dockerfile), str(workspace.path)], timeout)

    async def run_image(self, image, argv, timeout=60, resources=None, writable_workspace=False):
        name = "rr-check-" + image.rsplit(":", 1)[-1][:24]
        try:
            extra = []
            if writable_workspace:
                extra = ["--tmpfs", f"/workspace:rw,exec,nosuid,size={self.limits.workspace_mb}m,mode=1777"]
                argv = ["sh", "-c", 'cp -R /opt/rr-source/. /workspace/ && exec "$@"', "reporunner-check", *argv]
            return await run(["docker", "run", "--name", name, "--rm", "--label", "reporunner=true",
                *self.restrictions(False), *extra, "--env", "CI=true", "--env", "PYTHONDONTWRITEBYTECODE=1",
                *[part for name in (resources or {}) for part in ("--env", name)],
                image, "timeout", "--signal=KILL", f"{max(1,timeout):.1f}s", *argv], timeout + 3, extra_env=resources)
        finally:
            await self.remove(name)
