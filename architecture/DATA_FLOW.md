# Data flow and event consumption

1. Madhav creates a sanitized snapshot. `POST /v1/runs` accepts its repository context.
2. Supervisor emits `plan.created`; each task has a stable task ID within the round.
3. Workers emit `worker.started` and `worker.completed`; execution overlaps subject to
   the shared semaphore. Invalid worker output cancels sibling work.
4. Aggregation emits `aggregation.conflict` if needed, then `aggregation.accepted`.
5. Each execution request has a unique action ID, current snapshot, patches, one command,
   resource references, and remaining time. The response returns current context and output.
6. Failure starts another `round.started` with category and strategy-change state.
7. Command success triggers separate verification. `verification.completed` carries the
   check evidence and manifest for Likith's result display.
8. `approval.requested` pauses the loop. The frontend posts a matching approval ID.
   `approval.received` records the boolean decision, never resource secrets.
9. The terminal `run.completed`, `run.stopped`, or `run.failed` records grade and reason.

Budget operations emit `budget.step`. Recipe use emits `memory.hit`. Failed remote cleanup
emits `cleanup.failed` after the terminal event; the UI should still surface this event.

Poll `/events?after=N`. The endpoint returns all newer events in ascending sequence order.
Use `/replay` to export JSONL and replay by sequence, optionally pacing with timestamps.
Replay does not execute commands or call models. Timeline events deliberately contain
summaries rather than raw command output; full execution logs belong to Madhav's authorized
evidence service. Task objectives, commands, review reasons, and evidence metadata can still
be sensitive and are visible only through the authenticated supervisor API.
