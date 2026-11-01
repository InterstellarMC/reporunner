"""Deterministic orchestration demonstration. No LLM or Docker execution occurs."""
import asyncio
import json
from .controller import Controller
from .models import (Check, Command, Decision, Decomposition, EvidenceManifest, ExecutionResult,
                     HumanApproval, PatchProposal, RepositoryContext, Specialist,
                     VerificationResult, WorkerResult, WorkerTask)
from .providers import Router
from .store import Store

DIFF = "--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1,2 @@\n pytest\n+requests\n"


class DemoProvider:
    async def generate(self, instruction, payload, output):
        await asyncio.sleep(0)
        if output is Decomposition:
            repair = bool(payload["feedback"])
            return Decomposition(tasks=[WorkerTask(task_id="dependencies", specialist=Specialist.dependency,
                objective="Repair missing requests" if repair else "Analyze dependency setup",
                scope=["requirements.txt"], acceptance_criteria=["Dependencies support the tests"]),
                WorkerTask(task_id="environment", specialist=Specialist.environment,
                objective="Assess Python runtime", scope=["pyproject.toml"], acceptance_criteria=["Record runtime compatibility"])])
        if output is WorkerResult:
            task = payload["task"]
            patches = []
            if task["objective"] == "Repair missing requests":
                patches = [PatchProposal(task_id=task["task_id"], snapshot=payload["snapshot"],
                    files=["requirements.txt"], unified_diff=DIFF, reason="Add missing requests dependency")]
            return WorkerResult(task_id=task["task_id"], status="completed", patches=patches,
                evidence=["MOCK fixture finding"])
        if output is Decision:
            return Decision(accepted_task_ids=[r["task_id"] for r in payload["results"]],
                commands=[Command(argv=["python", "-m", "pytest"])], rationale="MOCK demo plan")
        raise ValueError(output)


class DemoExecution:
    def __init__(self, context):
        self.context, self.calls, self.closed = context, 0, []

    async def execute(self, request):
        self.calls += 1
        if request.patches:
            self.context = self.context.model_copy(update={"snapshot": "mock-repaired",
                "files": {**self.context.files, "requirements.txt": "pytest\nrequests\n"}})
        repaired = "requests" in self.context.files["requirements.txt"]
        return ExecutionResult(run_id=request.run_id, action_id=request.action_id, exit_code=0 if repaired else 1,
            stdout="MOCK test passed" if repaired else "", stderr="" if repaired else "ModuleNotFoundError: No module named 'requests'",
            context=self.context)

    async def verify(self, run_id, snapshot, remaining_seconds):
        return VerificationResult(run_id=run_id, snapshot=snapshot, checks=[
            Check(name="MOCK tests", kind="test", passed=True, evidence_ref="mock://tests"),
            Check(name="MOCK fresh rebuild", kind="rebuild", passed=True, evidence_ref="mock://rebuild")],
            evidence=EvidenceManifest(run_id=run_id, snapshot=snapshot,
                artifacts={"Dockerfile": "mock://Dockerfile", "RUN.md": "mock://RUN.md"}))

    async def close(self, run_id):
        self.closed.append(run_id)


def demo_context():
    return RepositoryContext(repository_url="https://example.invalid/mock-repo", snapshot="mock-initial",
        stack="python", files={"requirements.txt": "pytest\n", "pyproject.toml": "requires-python = '>=3.11'"})


async def approve_all(controller, task):
    """Demo/test helper only. Production never automatically approves."""
    while not task.done():
        for run_id, (approval_id, future) in list(controller.approvals.items()):
            if not future.done():
                controller.approve(run_id, HumanApproval(approval_id=approval_id, approved=True,
                                                        resource_refs=["mock-resource"]))
        await asyncio.sleep(0.001)


async def run_demo():
    context = demo_context()
    provider = DemoProvider()
    store = Store(":memory:")
    controller = Controller(Router(provider, {role: provider for role in Specialist}), DemoExecution(context), store)
    task = asyncio.create_task(controller.run(context))
    await approve_all(controller, task)
    view = await task
    print("MOCK DEMO ONLY — no live model, Docker, tests, rebuild, or human review occurred.")
    for event in store.events(view.run_id):
        print(event.model_dump_json())
    print(json.dumps({"mock_result": view.model_dump(mode="json")}, indent=2))


def main():
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
