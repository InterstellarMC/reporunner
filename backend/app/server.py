import asyncio
import json
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from ..orchestrator.controller import Controller
from ..orchestrator.local import LocalProvider
from ..orchestrator.models import HumanApproval, RunView, Specialist, Wire
from ..orchestrator.providers import ChatProvider, Router
from ..orchestrator.store import Store
from ..sandbox.execution import LocalExecution
from ..sandbox.limits import RepositoryLimits
from dataclasses import asdict
from ..verifier.detect import install_command, server_command
from ..sandbox.workspace import source_files

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT if (PROJECT_ROOT / "frontend/index.html").exists() else Path(__file__).resolve().parent / "assets"
DEFAULT_DATA = PROJECT_ROOT / ".reporunner" if (PROJECT_ROOT / "pyproject.toml").exists() else Path.home() / ".reporunner"


class Submission(Wire):
    repository_url: str = ""
    fixture: str | None = None
    mode: str = Field(default="local", pattern="^(local|live)$")
    network: bool = False
    resume_from: str | None = Field(default=None, pattern="^[a-f0-9]{32}$")
    max_steps: int = Field(default=25, ge=10, le=250)
    max_seconds: int = Field(default=600, ge=60, le=3600)


class ModelSettings(Wire):
    supervisor_url: str = Field(min_length=1, max_length=300)
    supervisor_model: str = Field(min_length=1, max_length=100)
    supervisor_key: str = Field(default="", max_length=500)
    worker_url: str = Field(min_length=1, max_length=300)
    worker_model: str = Field(min_length=1, max_length=100)
    worker_key: str = Field(default="", max_length=500)


class Resource(Wire):
    name: str
    value: str = Field(repr=False)


def now():
    return datetime.now(timezone.utc).isoformat()


class Application:
    def __init__(self, data: Path):
        self.data = data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.store = Store(str(self.data / "runs.db"))
        self.store.db.execute("CREATE TABLE IF NOT EXISTS records(run_id TEXT PRIMARY KEY, body TEXT)")
        self.execution = LocalExecution(self.data / "runs", ROOT / "fixtures", self.store)
        provider = LocalProvider()
        self.local_router = Router(provider, {r: provider for r in Specialist})
        self.live_router = None
        self.settings = {}
        if os.getenv("SUPERVISOR_BASE_URL") and os.getenv("SUPERVISOR_MODEL") and os.getenv("WORKER_BASE_URL") and os.getenv("WORKER_MODEL"):
            self.live_router = Router.from_env()
            self.settings = {"supervisor_url": os.environ["SUPERVISOR_BASE_URL"], "supervisor_model": os.environ["SUPERVISOR_MODEL"],
                "worker_url": os.environ["WORKER_BASE_URL"], "worker_model": os.environ["WORKER_MODEL"],
                "supervisor_key_configured": bool(os.getenv("SUPERVISOR_API_KEY")), "worker_key_configured": bool(os.getenv("WORKER_API_KEY"))}
        self.jobs, self.controllers, self.benchmarks = {}, {}, {}
        self.token = secrets.token_urlsafe(32)

    def record(self, run_id, **changes):
        row = self.store.db.execute("SELECT body FROM records WHERE run_id=?", (run_id,)).fetchone()
        data = json.loads(row[0]) if row else {}
        data.update(changes)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO records VALUES (?,?)", (run_id, json.dumps(data)))
        return data

    def details(self, run_id):
        view = self.store.view(run_id)
        directory = self.execution.root / run_id / "evidence"
        artifacts = [{"name": p.name, "bytes": p.stat().st_size, "url": f"/api/runs/{run_id}/artifacts/{p.name}"}
            for p in sorted(directory.iterdir()) if p.is_file()] if directory.exists() else []
        return {"run": view.model_dump(mode="json"), "metadata": self.record(run_id), "artifacts": artifacts}

    def launch(self, submission: Submission, evaluation=False):
        if len(self.jobs) >= 3:
            raise HTTPException(429, "Three runs are already active. Stop one or wait for completion.")
        if submission.mode == "live" and not self.live_router:
            raise HTTPException(422, "Configure the supervisor and worker endpoints in Model settings first.")
        if submission.resume_from:
            try:
                previous = self.details(submission.resume_from)
            except KeyError:
                raise HTTPException(404, "Checkpoint run not found")
            if submission.resume_from in self.jobs or previous["run"]["status"] in {"running", "awaiting_approval"}:
                raise HTTPException(409, "Stop or finish the previous run before continuing")
            if not (self.execution.root / submission.resume_from / "source").is_dir():
                raise HTTPException(409, "No source checkpoint is available")
            submission.repository_url = previous["metadata"].get("repository_url", "")
            submission.fixture = previous["metadata"].get("fixture")
        if not submission.fixture and not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", submission.repository_url):
            raise HTTPException(422, "Enter a public https://github.com/owner/repository URL.")
        if submission.fixture and submission.fixture not in {r["id"] for r in json.loads((ROOT / "fixtures/catalog.json").read_text())}:
            raise HTTPException(422, "Unknown example repository")
        run_id = uuid4().hex
        self.store.save(RunView(run_id=run_id, reason="Preparing isolated workspace"))
        self.record(run_id, **submission.model_dump(), started_at=now(), finished_at=None, elapsed_seconds=0,
            supervisor_model=self.settings.get("supervisor_model") if submission.mode == "live" else None,
            worker_model=self.settings.get("worker_model") if submission.mode == "live" else None,
            source="curated local fixture" if submission.fixture else "public GitHub repository",
            approval_policy="automatic fixture evaluation" if evaluation else "human review")
        self.store.emit(run_id, "run.queued", mode=submission.mode, fixture=submission.fixture)
        task = asyncio.create_task(self.run(run_id, submission, evaluation))
        self.jobs[run_id] = task
        task.add_done_callback(lambda _: self.jobs.pop(run_id, None))
        return run_id

    async def run(self, run_id, submission, evaluation=False):
        started = time.monotonic()
        try:
            async with asyncio.timeout(submission.max_seconds):
                resume_path = self.execution.root / submission.resume_from / "source" if submission.resume_from else None
                context = await self.execution.prepare(run_id, submission.repository_url, submission.fixture, submission.network, resume_path=resume_path)
                workspace = self.execution.workspaces[run_id]
                package = json.loads(context.files.get("package.json", "{}"))
                inventory, inventory_bytes = [], 0
                for path in source_files(workspace.path):
                    name = path.relative_to(workspace.path).as_posix()
                    inventory_bytes += len(name.encode())
                    if inventory_bytes > 40_000 or len(inventory) >= 1500:
                        break
                    inventory.append(name)
                environment = {"stack": context.stack, "test_argv": workspace.test_argv,
                    "baseline_argv": ["npm", "test"] if "test" in package.get("scripts", {}) else workspace.test_argv,
                    "server_argv": server_command(workspace.path, context.stack),
                    "repository_files": inventory,
                    "has_requirements": (workspace.path / "requirements.txt").exists(),
                    "install_argv": install_command(workspace.path, context.stack),
                    "has_test_script": "test" in package.get("scripts", {}),
                    "network_access": submission.network, "writable_paths": ["/workspace", "/tmp"],
                    "python_dependency_target": "/tmp/rr-deps", "http_port": 8000,
                    "instructions": f"Detected runtime: {context.stack}. Use its native toolchain and the supplied install_argv/test_argv, never assume Python. Use argv commands only. For Python only, install dependencies with --target /tmp/rr-deps. No apt, root, shell, host files, or test modification. Writable paths are /workspace and /tmp. The independent verifier runs original tests and a fresh build."}
                controller = Controller(self.live_router if submission.mode == "live" else self.local_router,
                    self.execution, self.store, max_steps=submission.max_steps, max_seconds=max(1, submission.max_seconds-(time.monotonic()-started)), environment=environment)
                self.controllers[run_id] = controller
                if evaluation:
                    # Explicit known-fixture evaluation policy; never applied to submitted GitHub repositories.
                    job = asyncio.create_task(controller.run(context, run_id))
                    try:
                        while not job.done():
                            pending = controller.approvals.get(run_id)
                            if pending and not pending[1].done():
                                view = self.store.view(run_id)
                                verified = bool(view.verification and view.verification.passes())
                                controller.approve(run_id, HumanApproval(approval_id=pending[0], approved=verified))
                            await asyncio.sleep(0.1)
                        await job
                    finally:
                        if not job.done():
                            job.cancel()
                            await asyncio.gather(job, return_exceptions=True)
                else:
                    feedback = ""
                    if submission.resume_from:
                        previous = self.details(submission.resume_from)
                        outputs = [e.data for e in self.store.events(submission.resume_from) if e.type == "execution.output"]
                        feedback = "Continue from the previous candidate. Previous stop: " + previous["run"]["reason"]
                        if outputs:
                            feedback += "\nLast actual command output: " + json.dumps(outputs[-1])[-8000:]
                        old_history = self.execution.root / submission.resume_from / "evidence/patches.json"
                        if old_history.exists():
                            workspace.history = json.loads(old_history.read_text())
                            shutil.copyfile(old_history, self.execution.evidence_dir(run_id) / "patches.json")
                            old_diff = old_history.with_name("changes.patch")
                            if old_diff.exists():
                                shutil.copyfile(old_diff, self.execution.evidence_dir(run_id) / "changes.patch")
                    await controller.run(context, run_id, initial_feedback=feedback)
        except asyncio.CancelledError:
            self.stop(run_id, "Run cancelled")
            raise
        except asyncio.TimeoutError:
            self.stop(run_id, "Configured time budget exhausted; source checkpoint is available to continue")
        except Exception as exc:
            # Acquisition and Docker errors are operational, not successful repairs.
            reason = str(exc)[:300] if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__ + ": execution service failed"
            self.stop(run_id, reason, failed=True)
        finally:
            self.controllers.pop(run_id, None)
            metadata = self.record(run_id, finished_at=now(), elapsed_seconds=round(time.monotonic()-started, 2))
            try:
                if self.execution.workspaces.get(run_id, None) and self.execution.workspaces[run_id].container:
                    await self.execution.close(run_id)
            except Exception:
                self.store.emit(run_id, "cleanup.failed", reason="operator cleanup required")
            self.execution.finalize(run_id, metadata)

    def stop(self, run_id, reason, failed=False):
        view = self.store.view(run_id)
        view.status, view.reason, view.pending_approval = "failed" if failed else "stopped", reason, None
        view.approval_kind = None
        self.store.save(view)
        self.store.emit(run_id, "run." + view.status, reason=reason, grade=view.grade)

    async def evaluate(self, benchmark_id):
        result = {"id": benchmark_id, "source": "10 curated local repositories; not a public-repository benchmark",
            "approval_policy": "Verified fixture results accepted automatically; resource blockers denied. This is an evaluation policy, not a senior human review.",
            "status": "running", "started_at": now(), "results": []}
        destination = self.data / "benchmarks"
        destination.mkdir(exist_ok=True)
        def save():
            (destination / (benchmark_id + ".json")).write_text(json.dumps(result, indent=2))
        save()
        try:
            for fixture in json.loads((ROOT / "fixtures/catalog.json").read_text()):
                while len(self.jobs) >= 3:
                    await asyncio.sleep(0.5)
                run_id = self.launch(Submission(fixture=fixture["id"]), evaluation=True)
                await self.jobs[run_id]
                details = self.details(run_id)
                result["results"].append({"fixture": fixture["id"], "title": fixture["title"], "expected": fixture["expected"],
                    "run_id": run_id, "status": details["run"]["status"], "grade": details["run"]["grade"],
                    "steps": details["run"]["steps"], "seconds": details["metadata"]["elapsed_seconds"], "reason": details["run"]["reason"]})
                save()
            result["status"] = "completed"
        except asyncio.CancelledError:
            result["status"] = "stopped"
            raise
        except Exception:
            result["status"] = "failed"
        finally:
            result["finished_at"] = now()
            save()


def create_application(data: Path | None = None):
    service = Application(data or Path(os.getenv("REPORUNNER_DATA", str(DEFAULT_DATA))))

    @asynccontextmanager
    async def lifespan(app):
        service.store.interrupt_orphans()
        yield
        jobs = list(service.benchmarks.values()) + list(service.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    app = FastAPI(title="RepoRunner", version="1.0.0", lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "RepoRunner is a local application; use localhost."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin access is disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def authorize(request: Request, rr_session: str = Cookie(default="")):
        if not secrets.compare_digest(rr_session, service.token):
            raise HTTPException(401, "Open RepoRunner in this browser to start a session")
        if request.method not in {"GET", "HEAD"} and request.headers.get("x-reporunner") != "1":
            raise HTTPException(403, "Missing request origin guard")

    api = FastAPI(dependencies=[Depends(authorize)])

    def details(run_id):
        try:
            return service.details(run_id)
        except KeyError:
            raise HTTPException(404, "Run not found")

    @api.get("/health")
    async def health():
        return {"docker": await service.execution.docker.health(), "live_configured": service.live_router is not None,
            "local_engine": "Local recipe engine — no LLM", "active_runs": len(service.jobs), "version": "1.0.0",
            "limits": {"repository": RepositoryLimits.from_env().describe(), "sandbox": asdict(service.execution.docker.limits)}}

    @api.get("/fixtures")
    async def fixtures():
        return json.loads((ROOT / "fixtures/catalog.json").read_text())

    @api.get("/runs")
    async def runs():
        ids = [row[0] for row in service.store.db.execute("SELECT run_id FROM runs ORDER BY rowid DESC LIMIT 100")]
        return [service.details(run_id) for run_id in ids]

    @api.post("/runs", status_code=202)
    async def start(submission: Submission):
        return {"run_id": service.launch(submission)}

    @api.get("/runs/{run_id}")
    async def status(run_id: str):
        return details(run_id)

    @api.get("/runs/{run_id}/events")
    async def events(run_id: str, after: int = 0):
        details(run_id)
        return service.store.events(run_id, max(0, after))

    @api.get("/runs/{run_id}/replay")
    async def replay(run_id: str):
        details(run_id)
        return Response("\n".join(e.model_dump_json() for e in service.store.events(run_id)) + "\n",
            media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="{run_id}-timeline.jsonl"'})

    @api.post("/runs/{run_id}/approval", status_code=202)
    async def approval(run_id: str, answer: HumanApproval):
        details(run_id)
        controller = service.controllers.get(run_id)
        if not controller:
            raise HTTPException(409, "This run is no longer waiting for approval")
        try:
            controller.approve(run_id, answer)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"accepted": True}

    @api.post("/runs/{run_id}/resources")
    async def resource(run_id: str, resource: Resource):
        view = details(run_id)["run"]
        if view["status"] != "awaiting_approval" or view.get("approval_kind") != "resource":
            raise HTTPException(409, "Resource provisioning is available only during a resource approval pause")
        try:
            ref = service.execution.provision(run_id, resource.name, resource.value)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"resource_ref": ref}

    @api.delete("/runs/{run_id}", status_code=202)
    async def cancel(run_id: str):
        details(run_id)
        job = service.jobs.get(run_id)
        if not job:
            raise HTTPException(409, "Run already finished")
        job.cancel()
        return {"cancelled": True}

    @api.get("/runs/{run_id}/artifacts/{filename}")
    async def artifact(run_id: str, filename: str):
        details(run_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise HTTPException(404)
        target = service.execution.root / run_id / "evidence" / filename
        if not target.is_file():
            raise HTTPException(404, "Artifact not available yet")
        if filename.endswith(".png"):
            return FileResponse(target, media_type="image/png")
        return FileResponse(target, filename=filename, media_type="application/octet-stream")

    @api.get("/settings")
    async def settings():
        return {"configured": service.live_router is not None, **service.settings}

    @api.put("/settings")
    async def update(settings: ModelSettings):
        for url in (settings.supervisor_url, settings.worker_url):
            if not re.match(r"^https?://[^\s]+$", url):
                raise HTTPException(422, "Model endpoints must be HTTP(S) URLs")
        supervisor = ChatProvider(settings.supervisor_url, settings.supervisor_model, settings.supervisor_key)
        worker = ChatProvider(settings.worker_url, settings.worker_model, settings.worker_key)
        service.live_router = Router(supervisor, {r: worker for r in Specialist})
        service.settings = {"supervisor_url": settings.supervisor_url, "supervisor_model": settings.supervisor_model,
            "worker_url": settings.worker_url, "worker_model": settings.worker_model,
            "supervisor_key_configured": bool(settings.supervisor_key), "worker_key_configured": bool(settings.worker_key)}
        return {"saved": True, "persistence": "memory only; restart clears these settings"}

    @api.post("/benchmarks", status_code=202)
    async def benchmark():
        if any(not task.done() for task in service.benchmarks.values()):
            raise HTTPException(409, "A benchmark is already running")
        benchmark_id = uuid4().hex
        service.benchmarks[benchmark_id] = asyncio.create_task(service.evaluate(benchmark_id))
        return {"benchmark_id": benchmark_id}

    @api.get("/benchmarks")
    async def benchmarks():
        folder = service.data / "benchmarks"
        return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)][:10] if folder.exists() else []

    app.mount("/api", api)
    app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")

    @app.get("/")
    async def index():
        response = FileResponse(ROOT / "frontend/index.html")
        response.set_cookie("rr_session", service.token, httponly=True, samesite="strict", max_age=86400)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def app_factory():
    return create_application()
