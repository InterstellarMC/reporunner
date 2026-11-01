# Shared wire contracts — draft 1.0

Pydantic models in `backend/orchestrator/models.py` are the source of truth. Run
`uv run reporunner-contracts` after an agreed interface change. CI detects schema drift.
Unknown object fields are rejected. Generated JSON Schema uses Draft 2020-12.
Runtime validation also enforces normalized relative paths, sensitive-path exclusions,
and a 200 KB total context budget, which JSON Schema alone cannot express.

| Contract | Producer | Consumer |
| --- | --- | --- |
| repository_context | Madhav's workspace service | Mudit's supervisor |
| decomposition / worker_task | Supervisor | Worker router |
| worker_result / patch_proposal | Specialists | Aggregator |
| decision | Aggregator | Controller |
| action_request | Controller | Madhav's execution service |
| execution_result | Execution service | Controller |
| verification_result / evidence_manifest | Verifier | Controller and Likith |
| run_event / run_view | Controller | Likith |
| human_approval | Likith's authenticated backend | Controller |

The `snapshot` is the exact current workspace revision, not necessarily just the initial
Git commit. Madhav must create a new snapshot identifier whenever candidate files change,
and return updated sanitized files. Patches always target one snapshot. One action contains
one command; patches are sent only on the first command of a plan. `action_id` must be used
as an idempotency key. The supervisor does not retry uncertain HTTP writes automatically.

Commands are argv arrays, never host shell snippets. The execution service must execute
them only inside the isolated workspace and enforce its own policy. `cwd` is repository
relative. `resource_refs` identify already provisioned service-side resources, never secrets.
`remaining_seconds` is the current run budget; command timeout must be capped accordingly.

Worker proposals are advisory. File overlap is conservatively treated as a conflict even
when line ranges differ. The aggregator can select a conflict-free subset; otherwise a
new decomposition round must resolve the conflict. Findings with identical keys and
different values are contradictory. The execution service still performs authoritative
patch syntax, path, symlink, scope, base snapshot, and application checks.

Events have a per-run monotonic `sequence`. Poll with `after` and append only unseen
sequences. All approval replies must use the current approval ID. No model can set a
verification grade. Artifact locations and evidence references must be treated as untrusted
display data by the UI and retrieved through an authorized artifact service.
