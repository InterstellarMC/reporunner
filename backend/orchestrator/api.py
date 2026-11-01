import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from .adapters import HttpExecutionAdapter
from .controller import Controller
from .models import HumanApproval, RepositoryContext, RunView
from .providers import Router
from .store import Store


def create_app(controller: Controller, api_token: str, max_active_runs: int = 4):
    if len(api_token) < 16:
        raise ValueError("set a SUPERVISOR_SERVICE_TOKEN of at least 16 characters")
    tasks = {}

    @asynccontextmanager
    async def lifespan(app):
        controller.store.interrupt_orphans()
        yield
        pending = list(tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + api_token):
            raise HTTPException(401, "invalid service token")

    app = FastAPI(title="RepoRunner Supervisor", version="1.0", lifespan=lifespan,
                  dependencies=[Depends(authorize)])

    def get_view(run_id):
        try:
            return controller.store.view(run_id)
        except KeyError:
            raise HTTPException(404, "unknown run")

    @app.post("/v1/runs", status_code=202, response_model=RunView)
    async def start(context: RepositoryContext):
        if len(tasks) >= max_active_runs:
            raise HTTPException(429, "run capacity reached")
        run_id = uuid4().hex
        view = RunView(run_id=run_id)
        controller.store.save(view)
        task = asyncio.create_task(controller.run(context, run_id))
        tasks[run_id] = task
        task.add_done_callback(lambda _: tasks.pop(run_id, None))
        return view

    @app.get("/v1/runs/{run_id}", response_model=RunView)
    async def status(run_id: str):
        return get_view(run_id)

    @app.get("/v1/runs/{run_id}/events")
    async def events(run_id: str, after: int = 0):
        get_view(run_id)
        return controller.store.events(run_id, max(0, after))

    @app.get("/v1/runs/{run_id}/replay")
    async def replay(run_id: str):
        get_view(run_id)
        body = "\n".join(e.model_dump_json() for e in controller.store.events(run_id)) + "\n"
        return Response(body, media_type="application/x-ndjson")

    @app.post("/v1/runs/{run_id}/approval", status_code=202)
    async def approval(run_id: str, answer: HumanApproval):
        get_view(run_id)
        try:
            controller.approve(run_id, answer)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"accepted": True}

    @app.delete("/v1/runs/{run_id}", status_code=202)
    async def cancel(run_id: str):
        get_view(run_id)
        task = tasks.get(run_id)
        if task is None:
            raise HTTPException(409, "run is no longer active")
        task.cancel()
        return {"cancelled": True}

    return app


def app_factory():
    return create_app(Controller(Router.from_env(), HttpExecutionAdapter(
        os.environ["EXECUTION_BASE_URL"], os.environ["EXECUTION_SERVICE_TOKEN"]),
        Store(os.getenv("REPORUNNER_DB", "reporunner.db"))), os.environ["SUPERVISOR_SERVICE_TOKEN"])
