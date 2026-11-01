"""Generate schema-valid integration samples and an explicitly mock replay."""
import asyncio
import json
from pathlib import Path
from backend.orchestrator.controller import Controller
from backend.orchestrator.demo import DemoExecution, DemoProvider, approve_all, demo_context, DIFF
from backend.orchestrator.models import ActionRequest, Command, HumanApproval, PatchProposal, Specialist
from backend.orchestrator.providers import Router
from backend.orchestrator.store import Store


async def generate():
    root = Path(__file__).resolve().parent
    context = demo_context()
    patch = PatchProposal(task_id="dependencies", snapshot=context.snapshot, files=["requirements.txt"],
                          unified_diff=DIFF, reason="MOCK missing dependency repair")
    action = ActionRequest(run_id="mock-run", action_id="mock-action", snapshot=context.snapshot,
        patches=[patch], commands=[Command(argv=["python", "-m", "pytest"])], remaining_seconds=300)
    execution = DemoExecution(context)
    result = await execution.execute(action)
    verification = await execution.verify(action.run_id, result.context.snapshot, 300)
    samples = {"repository_context": context, "action_request": action, "execution_result": result,
        "verification_result": verification, "human_approval": HumanApproval(approval_id="replace-with-pending-id", approved=True)}
    for name, sample in samples.items():
        (root / (name + ".json")).write_text(json.dumps(sample.model_dump(mode="json"), indent=2) + "\n")
    provider = DemoProvider()
    store = Store(":memory:")
    controller = Controller(Router(provider, {s: provider for s in Specialist}), DemoExecution(context), store)
    task = asyncio.create_task(controller.run(context, "mock-replay-run"))
    await approve_all(controller, task)
    await task
    (root / "mock-replay.jsonl").write_text("\n".join(e.model_dump_json() for e in store.events("mock-replay-run")) + "\n")


if __name__ == "__main__":
    asyncio.run(generate())
