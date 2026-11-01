import asyncio
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4
from ..evidence.generator import capture_html, dockerfile, scrub, write_bundle
from ..orchestrator.controller import classify
from ..orchestrator.models import Check, EvidenceManifest, ExecutionResult, VerificationResult
from ..verifier.detect import install_command, server_command, test_command, tests_passed
from .docker import Docker, DockerError
from .patches import PatchRejected, apply_patches, protected
from .process import run
from .workspace import Workspace, acquire_github, source_files
from .runtimes import RUNTIMES, detect_stack


class LocalExecution:
    def __init__(self, root: Path, fixtures: Path, store=None):
        self.root, self.fixtures, self.store = root.resolve(), fixtures.resolve(), store
        self.root.mkdir(parents=True, exist_ok=True)
        self.docker = Docker()
        self.workspaces = {}
        self.locks = {}

    def event(self, run_id, kind, **data):
        if self.store:
            self.store.emit(run_id, kind, **data)

    def evidence_dir(self, run_id):
        target = self.root / run_id / "evidence"
        target.mkdir(parents=True, exist_ok=True)
        return target

    async def prepare(self, run_id, repository_url="", fixture=None, network=False, resume_path=None):
        path = self.root / run_id / "source"
        path.mkdir(parents=True, exist_ok=False)
        if resume_path is not None:
            shutil.copytree(resume_path, path, dirs_exist_ok=True)
            self.event(run_id, "repository.resumed", source="previous candidate checkpoint")
        elif fixture:
            if not re.fullmatch(r"[a-z0-9-]+", fixture) or not (self.fixtures / fixture).is_dir():
                raise ValueError("unknown example repository")
            shutil.copytree(self.fixtures / fixture, path, dirs_exist_ok=True)
            repository_url = "fixture://" + fixture
        else:
            self.event(run_id, "repository.downloading", repository_url=repository_url)
            sha = await acquire_github(repository_url, path)
            self.event(run_id, "repository.resolved", revision=sha)
        workspace = Workspace(run_id, path, repository_url, network, fixture)
        context = workspace.context()
        if context.stack not in RUNTIMES:
            raise ValueError("No supported root runtime manifest found. Supported: Python, Node/static HTML, Go, Rust, Maven Java, .NET. Nested projects require a root manifest; GPU, Compose, and other toolchains need an explicit runtime integration.")
        workspace.test_argv = test_command(path, context.stack)
        workspace.initial_tests = {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_files(path) if protected(p.relative_to(path).as_posix())}
        self.workspaces[run_id] = workspace
        self.locks[run_id] = asyncio.Lock()
        self.event(run_id, "sandbox.creating", stack=context.stack, network=network)
        await self.docker.create(workspace, context.stack)
        self.event(run_id, "sandbox.ready", image=workspace.image, isolated=True,
            network="package access enabled" if network else "disabled", memory_mb=self.docker.limits.memory_mb, workspace_mb=self.docker.limits.workspace_mb, cpus=1)
        return context

    def scoped_files(self, run_id, paths):
        """Read requested source paths without expanding the model's whole context."""
        from ..orchestrator.models import safe_path
        workspace = self.workspaces[run_id]
        files, total = {}, 0
        for name in paths:
            path = workspace.path / safe_path(name)
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 30_000:
                try:
                    content = path.read_text()
                except UnicodeDecodeError:
                    continue
                total += len(content.encode())
                if total <= 180_000:
                    files[name] = content
        return files

    def setup_command(self, run_id):
        workspace = self.workspaces[run_id]
        return install_command(workspace.path, detect_stack(workspace.path))

    def log(self, workspace, title, output):
        directory = self.evidence_dir(workspace.run_id)
        with (directory / "execution.log").open("a") as log:
            log.write(f"\n--- {title} ---\nexit_code={output.code} timed_out={output.timed_out}\n")
            log.write(self.redact(workspace, output.stdout) + "\n" + self.redact(workspace, output.stderr) + "\n")

    def redact(self, workspace, text):
        for value in workspace.resources.values():
            text = text.replace(value, "[REDACTED]")
        return scrub(text)

    def provision(self, run_id, name, value):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name) or not re.search(r"(?:KEY|TOKEN|PASSWORD|SECRET|URL)$", name):
            raise ValueError("resource names must be uppercase credential/config names ending KEY, TOKEN, PASSWORD, SECRET, or URL")
        if not 4 <= len(value) <= 4096 or "\x00" in value:
            raise ValueError("resource value must contain 4 to 4096 characters")
        workspace = self.workspaces[run_id]
        workspace.resources[name] = value
        ref = "resource:" + uuid4().hex
        workspace.resource_refs.add(ref)
        return ref

    async def execute(self, request):
        workspace = self.workspaces[request.run_id]
        async with self.locks[request.run_id]:
            if request.action_id in workspace.actions:
                return workspace.actions[request.action_id]
            context = workspace.context()
            if request.snapshot != context.snapshot:
                raise ValueError("execution request targets stale snapshot")
            if not set(request.resource_refs) <= workspace.resource_refs:
                raise ValueError("resource reference is not authorized for this run")
            if request.patches:
                try:
                    changes = apply_patches(workspace.path, request.patches, request.snapshot)
                except ValueError as exc:
                    # Distinguish patch failures from Docker or protocol errors so the
                    # supervisor can safely retry against the unchanged source.
                    reason = str(exc).strip() or "patch rejected"
                    raise PatchRejected(reason) from exc
                # Reset candidate container after source changes. Untracked mutations from a prior
                # command cannot survive the source snapshot boundary.
                await self.docker.remove(workspace.container)
                workspace.container = None
                await self.docker.create(workspace, context.stack)
                workspace.history.extend(p.model_dump(mode="json") for p in request.patches)
                directory = self.evidence_dir(request.run_id)
                (directory / "changes.patch").write_text("\n".join(p["unified_diff"] for p in workspace.history))
                (directory / "patches.json").write_text(json.dumps(workspace.history, indent=2))
                self.event(request.run_id, "patch.applied", files=list(changes), snapshot=workspace.context().snapshot)
            command = request.commands[0]
            timeout = min(command.timeout_seconds, request.remaining_seconds, 600)
            self.event(request.run_id, "execution.started", action_id=request.action_id,
                       argv=command.argv, cwd=command.cwd, timeout_seconds=timeout)
            output = await self.docker.execute(workspace, command.argv, command.cwd, timeout)
            self.log(workspace, "COMMAND " + " ".join(command.argv), output)
            stdout, stderr = self.redact(workspace, output.stdout), self.redact(workspace, output.stderr)
            category = classify(stderr + stdout)
            blocker = category if category in {"secret", "gpu", "private_package"} and output.code else None
            self.event(request.run_id, "execution.output", action_id=request.action_id,
                       stdout=stdout[-18000:], stderr=stderr[-18000:], exit_code=output.code)
            result = ExecutionResult(run_id=request.run_id, action_id=request.action_id, exit_code=output.code,
                stdout=stdout, stderr=stderr, timed_out=output.timed_out, context=workspace.context(), blocker=blocker)
            workspace.actions[request.action_id] = result
            return result

    async def probe_http(self, workspace, stack, launch, timeout=15):
        if stack == "python":
            launcher = ("import subprocess,json; f=open('/tmp/rr-http.log','w'); "
                f"p=subprocess.Popen({launch!r},stdout=f,stderr=f); print(p.pid)")
            started = await self.docker.execute(workspace, ["python", "-c", launcher], timeout=5)
            probe = ["python", "-c", "import urllib.request,json; r=urllib.request.urlopen('http://127.0.0.1:8000',timeout=2); print(json.dumps({'status':r.status,'html':r.read(20000).decode(errors='replace')}))"]
        else:
            launcher = ("const fs=require('fs');const c=require('child_process');let f=fs.openSync('/tmp/rr-http.log','w');"
                f"let p=c.spawn({json.dumps(launch[0])},{json.dumps(launch[1:])},{{stdio:['ignore',f,f],detached:true}});p.unref();console.log(p.pid)")
            started = await self.docker.execute(workspace, ["node", "-e", launcher], timeout=5)
            probe = ["node", "-e", "fetch('http://127.0.0.1:8000',{signal:AbortSignal.timeout(2000)}).then(async r=>console.log(JSON.stringify({status:r.status,html:(await r.text()).slice(0,20000)}))).catch(e=>{console.error(e.message);process.exit(1)})"]
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            output = await self.docker.execute(workspace, probe, timeout=4)
            if output.code == 0:
                try:
                    response = json.loads(output.stdout.strip())
                    if response.get("status") == 200:
                        return True, response.get("html", ""), "HTTP 200 at http://127.0.0.1:8000"
                except (ValueError, TypeError):
                    pass
            await asyncio.sleep(0.5)
        logs = await self.docker.execute(workspace, ["cat", "/tmp/rr-http.log"], timeout=3)
        return False, "", logs.stdout + logs.stderr

    async def verify(self, run_id, expected_snapshot, remaining_seconds):
        workspace = self.workspaces[run_id]
        context = workspace.context()
        if context.snapshot != expected_snapshot:
            raise ValueError("verification snapshot changed")
        directory = self.evidence_dir(run_id)
        checks, artifacts = [], {}
        started = time.monotonic()
        budget = lambda: max(1, remaining_seconds - (time.monotonic() - started))
        self.event(run_id, "verification.started", snapshot=expected_snapshot)
        for path, digest in workspace.initial_tests.items():
            p = workspace.path / path
            if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                raise ValueError("original tests were modified")
        launch = server_command(workspace.path, context.stack)
        if workspace.test_argv:
            output = await self.docker.execute(workspace, workspace.test_argv, timeout=min(300, budget()))
            self.log(workspace, "INDEPENDENT TESTS", output)
            (directory / "tests.log").write_text(self.redact(workspace, output.stdout + "\n" + output.stderr))
            checks.append(Check(name="Original repository tests", kind="test", passed=tests_passed(output), evidence_ref="tests.log"))
        elif launch:
            ok, html, detail = await self.probe_http(workspace, context.stack, launch, min(12, budget()))
            (directory / "http.log").write_text(self.redact(workspace, detail))
            checks.append(Check(name="Sandbox HTTP 200", kind="http", passed=ok, evidence_ref="http.log"))
            if ok:
                (directory / "http-response.html").write_text(self.redact(workspace, html))
                if await capture_html(self.redact(workspace, html), directory / "screenshot.png"):
                    artifacts["screenshot.png"] = "screenshot.png"
        else:
            (directory / "tests.log").write_text("No supported independent test suite or HTTP entrypoint was found.")
            checks.append(Check(name="Independent check unavailable", kind="test", passed=False, evidence_ref="tests.log"))
        final_command = workspace.test_argv or launch or ["python", "--version"]
        (directory / "Dockerfile").write_text(dockerfile(workspace, context.stack, final_command))
        instructions = ("# Run this verified candidate\n\n"
            f"Source: `{workspace.repository_url}`\n\nSnapshot: `{expected_snapshot}`\n\n"
            "Unzip the evidence bundle. From its root, run:\n\n"
            "```sh\ndocker build -t reporunner-candidate -f Dockerfile source\n"
            f"docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true --memory {self.docker.limits.memory_mb}m --cpus 1 reporunner-candidate\n```\n\n"
            "For a web entrypoint, publish its port explicitly on a trusted Docker network. "
            "The independent HTTP probe in this run uses port 8000 inside the sandbox.\n\n"
            "Check evidence.json and the test/rebuild logs for actual outcomes. A generated "
            "Dockerfile alone is not a success claim.\n")
        (directory / "RUN.md").write_text(instructions)
        tag = "reporunner-rebuild:" + run_id[:24]
        rebuild_ok = False
        try:
            self.event(run_id, "rebuild.started", snapshot=expected_snapshot)
            built = await self.docker.build(workspace, directory / "Dockerfile", tag, min(600, budget()))
            self.log(workspace, "FRESH IMAGE BUILD", built)
            (directory / "rebuild.log").write_text(scrub(built.stdout + "\n" + built.stderr))
            if built.code == 0:
                if workspace.test_argv:
                    fresh = await self.docker.run_image(tag, workspace.test_argv, min(300, budget()), workspace.resources,
                        **({"writable_workspace": True} if context.stack in {"go", "rust", "java", "dotnet"} else {}))
                    self.log(workspace, "FRESH CONTAINER TESTS", fresh)
                    with (directory / "rebuild.log").open("a") as log:
                        log.write("\nFRESH CONTAINER CHECK\n" + self.redact(workspace, fresh.stdout + fresh.stderr))
                    rebuild_ok = tests_passed(fresh)
                elif launch:
                    # Probe the built image in a new container without reusing candidate state.
                    fresh_ws = Workspace(run_id + "fresh", workspace.path, workspace.repository_url)
                    fresh_ws.resources = workspace.resources
                    fresh_name = "rr-fresh-" + run_id[:24]
                    result = await run(["docker", "run", "-d", "--name", fresh_name, "--label", "reporunner=true",
                        *self.docker.restrictions(False), "--env", "PORT=8000", tag, "sleep", "60"], 10)
                    if not result.code:
                        fresh_ws.container = fresh_name
                        try:
                            rebuild_ok, _, detail = await self.probe_http(fresh_ws, context.stack, launch, min(12, budget()))
                            with (directory / "rebuild.log").open("a") as log:
                                log.write("\nFRESH CONTAINER HTTP CHECK\n" + scrub(detail))
                        finally:
                            await self.docker.remove(fresh_name)
            checks.append(Check(name="Fresh image build + independent rerun", kind="rebuild", passed=rebuild_ok, evidence_ref="rebuild.log"))
            self.event(run_id, "rebuild.completed", passed=rebuild_ok)
        finally:
            await run(["docker", "image", "rm", "-f", tag], 15)
        artifacts.update({p.name: p.name for p in directory.iterdir() if p.is_file() and p.name != "evidence.zip"})
        result = VerificationResult(run_id=run_id, snapshot=expected_snapshot, checks=checks,
            evidence=EvidenceManifest(run_id=run_id, snapshot=expected_snapshot, artifacts=artifacts),
            diagnostic="\n".join((directory / name).read_text()[-6000:] for name in ("tests.log", "http.log", "rebuild.log")
                if (directory / name).exists() and not all(c.passed for c in checks))[-12000:])
        write_bundle(workspace, directory, result)
        return result

    async def close(self, run_id):
        workspace = self.workspaces.get(run_id)
        if workspace:
            await self.docker.remove(workspace.container)
            workspace.container = None
            self.event(run_id, "sandbox.closed")
            directory = self.evidence_dir(run_id)
            if self.store:
                (directory / "timeline.jsonl").write_text("\n".join(e.model_dump_json() for e in self.store.events(run_id)) + "\n")
                (directory / "result.json").write_text(self.store.view(run_id).model_dump_json(indent=2))
            write_bundle(workspace, directory)
            workspace.resources.clear()
            workspace.resource_refs.clear()

    def finalize(self, run_id, metadata):
        """Always leave a downloadable report, including acquisition/provider failures."""
        directory = self.evidence_dir(run_id)
        view = self.store.view(run_id)
        (directory / "result.json").write_text(view.model_dump_json(indent=2) + "\n")
        (directory / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (directory / "timeline.jsonl").write_text("\n".join(e.model_dump_json() for e in self.store.events(run_id)) + "\n")
        events = self.store.events(run_id)
        lines = ["# RepoRunner execution report", "", f"Repository: {metadata.get('repository_url') or metadata.get('fixture')}",
                 f"Status: {view.status}; verdict: {view.grade}", f"Actions: {view.steps}/{metadata.get('max_steps', 25)}; rounds: {view.rounds}",
                 f"Elapsed: {metadata.get('elapsed_seconds', 0)} seconds", "", "## Outcome", "", view.reason or "No outcome recorded", "", "## Attempts", ""]
        if metadata.get("resume_from"):
            lines += ["Resumed from candidate run: " + metadata["resume_from"], ""]
        for event in events:
            data = event.data
            if event.type == "repository.resolved":
                lines += ["Source revision: " + str(data.get("revision", "")), ""]
            elif event.type == "run.started":
                lines += ["Source snapshot: " + str(data.get("snapshot", "")), ""]
            elif event.type == "sandbox.ready":
                lines += ["Sandbox: " + json.dumps(data), ""]
            elif event.type == "round.started":
                lines += [f"### Round {data['round']} — {data.get('category', 'repository')}", ""]
            elif event.type == "aggregation.accepted":
                lines += [data.get("rationale", "Command plan selected"), ""]
            elif event.type == "execution.started":
                lines += ["Command: `" + " ".join(data.get("argv", [])) + "`", ""]
            elif event.type == "execution.output":
                lines += [f"Exit code: {data.get('exit_code')}", "```text", (data.get("stderr") or data.get("stdout") or "(no output)")[-4000:], "```", ""]
            elif event.type == "patch.applied":
                lines += ["Patched: " + ", ".join(data.get("files", [])), ""]
            elif event.type == "model.completed":
                lines += [f"Model: {data.get('provider')}; role: {data.get('role')}; usage: {data.get('usage', {})}", ""]
            elif event.type == "model.failed":
                lines += [f"Model call failed: {data.get('role')}; error: {data.get('error')}; reported usage: {data.get('usage', {})}", ""]
            elif event.type == "recovery.replan":
                lines += ["Recovery: " + data.get("reason", ""), ""]
            elif event.type == "memory.hit":
                lines += ["A verified recipe from a matching repository context was reused.", ""]
            elif event.type == "approval.requested":
                lines += ["Human input requested: " + data.get("reason", ""), ""]
        lines += ["## Independent verification", ""]
        if view.verification:
            lines += [f"- {'PASS' if c.passed else 'FAIL'}: {c.name} ({c.evidence_ref})" for c in view.verification.checks]
        else:
            lines += ["Independent verification was not completed."]
        lines += ["", "Full ordered events: timeline.jsonl. Command output: execution.log. Changes: changes.patch when present.",
                  "A partial result is preserved as a candidate, not a successful repair. Use Continue from checkpoint to resume after addressing the blocker." if view.status != "completed" else "The independent checks passed and the candidate was accepted."]
        (directory / "REPORT.md").write_text(scrub("\n".join(lines)) + "\n")
        if view.status != "completed":
            (directory / "failure_report.md").write_text("\n".join(lines) + "\n")
        workspace = self.workspaces.get(run_id) or Workspace(run_id, self.root / run_id / "source", metadata.get("repository_url", ""))
        write_bundle(workspace, directory)
