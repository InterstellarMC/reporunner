import json
from pathlib import Path
from . import models

CONTRACTS = {
    "repository_context": models.RepositoryContext, "worker_task": models.WorkerTask,
    "worker_result": models.WorkerResult, "patch_proposal": models.PatchProposal,
    "action_request": models.ActionRequest, "execution_result": models.ExecutionResult,
    "verification_result": models.VerificationResult, "run_event": models.RunEvent,
    "human_approval": models.HumanApproval, "evidence_manifest": models.EvidenceManifest,
    "decomposition": models.Decomposition, "decision": models.Decision, "run_view": models.RunView,
}


def main():
    root = Path.cwd() / "contracts"
    root.mkdir(exist_ok=True)
    for name, model in CONTRACTS.items():
        (root / (name + ".schema.json")).write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")


if __name__ == "__main__":
    main()
