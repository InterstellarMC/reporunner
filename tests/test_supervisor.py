import asyncio
import json
from pathlib import Path
import httpx
import pytest
from jsonschema import Draft202012Validator
from backend.orchestrator.aggregation import conflicts, validate_result
from backend.orchestrator.api import create_app
from backend.orchestrator.context import build_context
from backend.orchestrator.contracts import CONTRACTS
from backend.orchestrator.controller import Controller, classify
from backend.orchestrator.demo import (DemoExecution, DemoProvider, DIFF, approve_all, demo_context)
from backend.orchestrator.models import (Command, Decision, Decomposition, HumanApproval,
    PatchProposal, Specialist, WorkerResult, WorkerTask)
from backend.orchestrator.providers import ChatProvider, Router
from backend.orchestrator.store import Store
from backend.sandbox.patches import PatchRejected


def setup(provider=None, execution=None, store=None, **kwargs):
    provider = provider or DemoProvider()
    context = demo_context()
    return Controller(Router(provider, {s: provider for s in Specialist}),
        execution or DemoExecution(context), store or Store(":memory:"), **kwargs)


async def complete(controller):
    task = asyncio.create_task(controller.run(demo_context()))
    await approve_all(controller, task)
    return await task


async def wait_approval(controller, task):
    async with asyncio.timeout(2):
        while not controller.approvals and not task.done():
            await asyncio.sleep(0.001)
    assert controller.approvals
    return next(iter(controller.approvals.items()))


async def test_recursive_repair_and_recipe_reuse():
    store = Store(":memory:")
    first = setup(store=store)
    view = await complete(first)
    assert view.status == "completed" and view.grade == "GREEN"
    assert view.rounds == 2
    second = setup(store=store)
    repeated = await complete(second)
    assert repeated.grade == "GREEN" and repeated.steps < view.steps
    assert "memory.hit" in [e.type for e in store.events(repeated.run_id)]
    assert first.execution.closed == [view.run_id]
    events = store.events(view.run_id)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))


async def test_final_review_pauses_and_rejects_stale_approval():
    c = setup()
    task = asyncio.create_task(c.run(demo_context()))
    run_id, (approval_id, _) = await wait_approval(c, task)
    view = c.store.view(run_id)
    assert view.grade == "AMBER" and view.status == "awaiting_approval"
    assert view.approval_kind == "final"
    with pytest.raises(ValueError):
        c.approve(run_id, HumanApproval(approval_id="wrong", approved=True))
    c.approve(run_id, HumanApproval(approval_id=approval_id, approved=False))
    view = await task
    assert view.status == "stopped" and view.grade != "GREEN"
    assert c.store.db.execute("SELECT count(*) FROM recipes").fetchone()[0] == 0


async def test_step_limit_and_cleanup():
    c = setup(max_steps=3)
    result = await c.run(demo_context())
    assert result.steps == 3 and result.status == "stopped"
    assert "step budget" in result.reason and c.execution.calls == 0
    assert c.execution.closed == [result.run_id]


async def test_insufficient_parallel_worker_budget_stops_cleanly():
    c = setup(max_steps=2)
    view = await c.run(demo_context())
    assert view.status == "stopped" and "step budget" in view.reason
    assert c.execution.calls == 0 and view.steps <= 2


async def test_cancellation_requests_cleanup():
    c = setup()
    task = asyncio.create_task(c.run(demo_context()))
    run_id, _ = await wait_approval(c, task)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert c.store.view(run_id).status == "stopped"
    assert c.execution.closed == [run_id] and not c.approvals


async def test_execution_adapter_transport(monkeypatch):
    from backend.orchestrator.adapters import HttpExecutionAdapter
    from backend.orchestrator.models import ActionRequest
    request = ActionRequest(run_id="r", action_id="a", snapshot="s", patches=[],
                            commands=[Command(argv=["python", "--version"])], remaining_seconds=10)
    fixture = DemoExecution(demo_context())
    result = await fixture.execute(request)
    verification = await fixture.verify("r", result.context.snapshot, 10)
    paths = []
    def handle(req):
        assert req.headers["Authorization"] == "Bearer test-execution-token"
        paths.append(req.url.path)
        if req.url.path.endswith("execute"):
            assert json.loads(req.content)["action_id"] == "a"
            return httpx.Response(200, json=result.model_dump(mode="json"))
        if req.url.path.endswith("verify"):
            return httpx.Response(200, json=verification.model_dump(mode="json"))
        return httpx.Response(200, json={"closed": True})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    adapter = HttpExecutionAdapter("http://execution", "test-execution-token")
    assert (await adapter.execute(request)).action_id == "a"
    assert (await adapter.verify("r", result.context.snapshot, 10)).snapshot == result.context.snapshot
    await adapter.close("r")
    assert paths == ["/v1/execute", "/v1/verify", "/v1/close"]


async def test_timeout_during_provider_call():
    class Slow(DemoProvider):
        async def generate(self, *args):
            await asyncio.sleep(2)
    c = setup(provider=Slow(), max_seconds=0.01)
    view = await c.run(demo_context())
    assert view.status == "stopped" and "time budget" in view.reason


async def test_timeout_during_human_wait():
    c = setup(max_seconds=0.05)
    view = await c.run(demo_context())
    assert view.status == "stopped" and not c.approvals
    assert view.grade != "GREEN"


class AlwaysFails(DemoExecution):
    async def execute(self, request):
        result = await super().execute(request)
        return result.model_copy(update={"exit_code": 1, "stderr": "build failed", "stdout": ""})


async def test_repeated_errors_change_strategy_then_stop():
    class Changing(DemoProvider):
        def __init__(self):
            self.changes = []
        async def generate(self, instruction, payload, output):
            if output is Decomposition:
                self.changes.append(payload["strategy_change"])
                result = await super().generate(instruction, payload, output)
                # No fake repair patch on a generic build error.
                for task in result.tasks:
                    task.objective = "Investigate build"
                return result
            if output is Decision:
                result = await super().generate(instruction, payload, output)
                if self.changes[-1]:
                    result.commands = [Command(argv=["python3", "-m", "pytest"])]
                return result
            return await super().generate(instruction, payload, output)
    provider = Changing()
    c = setup(provider=provider, execution=AlwaysFails(demo_context()))
    view = await c.run(demo_context())
    assert provider.changes == [False, False, True]
    assert "same error" in view.reason and view.steps <= 25


async def test_repeated_identical_patch_is_not_a_strategy_change():
    c = setup(execution=AlwaysFails(demo_context()))
    view = await c.run(demo_context())
    assert view.status == "stopped" and "failed to change strategy" in view.reason
    assert c.execution.calls == 2


async def test_rejected_patch_is_reported_and_sent_back_for_recovery():
    class RejectPatchOnce(DemoExecution):
        rejected = False
        async def execute(self, request):
            if request.patches and not self.rejected:
                self.rejected = True
                raise PatchRejected("patch context mismatch")
            return await super().execute(request)

    execution = RejectPatchOnce(demo_context())
    c = setup(execution=execution)
    view = await complete(c)
    assert view.status == "completed" and view.grade == "GREEN"
    events = c.store.events(view.run_id)
    rejected = next(event for event in events if event.type == "patch.rejected")
    assert rejected.data["reason"] == "patch context mismatch"
    assert any(event.type == "round.started" for event in events if event.sequence > rejected.sequence)


async def test_missing_secret_pauses_then_resource_approval_resumes():
    class Secret(DemoExecution):
        async def execute(self, request):
            if not request.resource_refs:
                result = await super().execute(request)
                return result.model_copy(update={"blocker": "secret", "stderr": "missing API key"})
            return await super().execute(request)
    c = setup(execution=Secret(demo_context()))
    task = asyncio.create_task(c.run(demo_context()))
    run_id, (approval_id, _) = await wait_approval(c, task)
    assert c.execution.calls == 1
    assert c.store.view(run_id).approval_kind == "resource"
    c.approve(run_id, HumanApproval(approval_id=approval_id, approved=True, resource_refs=["vault:demo-key"]))
    await approve_all(c, task)
    assert (await task).grade == "GREEN"


async def test_verification_failure_cannot_be_green():
    class NoRebuild(DemoExecution):
        async def verify(self, *args):
            result = await super().verify(*args)
            result.checks = [c for c in result.checks if c.kind != "rebuild"]
            return result
    c = setup(execution=NoRebuild(demo_context()))
    result = await c.run(demo_context())
    assert result.grade != "GREEN" and not c.approvals


async def test_wrong_snapshot_verification_rejected():
    class Wrong(DemoExecution):
        async def verify(self, *args):
            result = await super().verify(*args)
            result.snapshot = "stale"
            return result
    c = setup(execution=Wrong(demo_context()))
    result = await c.run(demo_context())
    assert result.status == "stopped" and "current run" in result.reason


async def test_workers_run_concurrently():
    class Parallel(DemoProvider):
        running = 0
        peak = 0
        async def generate(self, instruction, payload, output):
            if output is WorkerResult:
                self.running += 1
                self.peak = max(self.peak, self.running)
                await asyncio.sleep(0.01)
                self.running -= 1
            return await super().generate(instruction, payload, output)
    provider = Parallel()
    await complete(setup(provider=provider))
    assert provider.peak == 2


async def test_malformed_provider_response_fails_closed():
    class Broken(DemoProvider):
        async def generate(self, *args):
            raise ValueError("secret-that-must-not-appear")
    c = setup(provider=Broken())
    view = await c.run(demo_context())
    assert view.status == "failed" and "secret-that" not in view.reason


def test_patch_scope_snapshot_and_conflicts():
    task = WorkerTask(task_id="a", specialist="dependency", objective="fix",
        scope=["requirements.txt"], acceptance_criteria=["tests pass"])
    patch = PatchProposal(task_id="a", snapshot="old", files=["requirements.txt"], unified_diff=DIFF, reason="fix")
    result = WorkerResult(task_id="a", status="completed", patches=[patch])
    validate_result(task, result, "old")
    with pytest.raises(ValueError):
        validate_result(task, result, "new")
    patch.unified_diff = DIFF.replace("requirements.txt", "other.txt")
    with pytest.raises(ValueError):
        validate_result(task, result, "old")
    patch.unified_diff = DIFF
    assert conflicts([result, result])
    assert conflicts([WorkerResult(task_id="a", status="completed", findings={"python": "3.11"}),
        WorkerResult(task_id="b", status="completed", findings={"python": "3.12"})])


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", ".env", "a/.git/config", "a\\b", "a//b", "key.pem"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError):
        PatchProposal(task_id="a", snapshot="x", files=[path], unified_diff=DIFF, reason="bad")


def test_context_excludes_secrets_symlinks_and_large_files(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest")
    (tmp_path / ".env").write_text("SECRET=never-read")
    (tmp_path / "README.md").symlink_to(tmp_path / ".env")
    (tmp_path / "setup.py").write_text("x" * 31_000)
    assert build_context(tmp_path, "demo", "snapshot").files == {"requirements.txt": "pytest"}


@pytest.mark.parametrize("error,kind", [("missing API key", "secret"), ("CUDA unavailable", "gpu"),
    ("private package", "private_package"), ("ModuleNotFoundError", "dependency"),
    ("libGL.so.1 missing", "environment"), ("database connection refused", "configuration"),
    ("AssertionError", "test")])
def test_error_classification(error, kind):
    assert classify(error) == kind


async def test_authenticated_api_approval_events_and_replay():
    c = setup()
    token = "unit-test-token-at-least-16"
    app = create_app(c, token)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/v1/runs", json=demo_context().model_dump())).status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        response = await client.post("/v1/runs", json=demo_context().model_dump())
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        async with asyncio.timeout(2):
            while True:
                view = (await client.get(f"/v1/runs/{run_id}")).json()
                if view["pending_approval"]:
                    break
                await asyncio.sleep(0.001)
        path = f"/v1/runs/{run_id}/approval"
        assert (await client.post(path, json={"approval_id": "old", "approved": True})).status_code == 409
        assert (await client.post(path, json={"approval_id": view["pending_approval"], "approved": True})).status_code == 202
        async with asyncio.timeout(2):
            while (await client.get(f"/v1/runs/{run_id}")).json()["status"] != "completed":
                await asyncio.sleep(0.001)
        replay = await client.get(f"/v1/runs/{run_id}/replay")
        assert json.loads(replay.text.splitlines()[-1])["type"] == "run.completed"
        assert (await client.get(f"/v1/runs/{run_id}/events?after=999")).json() == []


async def test_chat_provider_json_validation(monkeypatch):
    def handle(request):
        body = json.loads(request.content)
        assert "untrusted" in body["messages"][0]["content"]
        assert body["model"] == "test-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tasks": [], "human_reason": "needs GPU"}'}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    result = await ChatProvider("http://model/v1", "test-model").generate("supervisor", {}, Decomposition)
    assert result.human_reason == "needs GPU"


def test_generated_contracts_match_runtime():
    root = Path(__file__).resolve().parents[1] / "contracts"
    for name, model in CONTRACTS.items():
        schema = json.loads((root / (name + ".schema.json")).read_text())
        assert schema == model.model_json_schema()
        Draft202012Validator.check_schema(schema)


def test_examples_match_contracts():
    root = Path(__file__).resolve().parents[1] / "examples"
    for path in root.glob("*.json"):
        CONTRACTS[path.stem].model_validate_json(path.read_text())
    for line in (root / "mock-replay.jsonl").read_text().splitlines():
        CONTRACTS["run_event"].model_validate_json(line)


def test_restart_marks_orphan_stopped_and_retains_replay(tmp_path):
    from backend.orchestrator.models import RunView
    path = str(tmp_path / "runs.db")
    s = Store(path)
    s.save(RunView(run_id="orphan", status="awaiting_approval"))
    s.emit("orphan", "approval.requested", approval_id="old")
    s.db.close()
    restored = Store(path)
    restored.interrupt_orphans()
    assert restored.view("orphan").status == "stopped"
    assert len(restored.events("orphan")) == 2
