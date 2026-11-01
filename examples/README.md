# Integration samples

The schema samples and `mock-replay.jsonl` are mock examples. `recorded-docker-run.jsonl` is a recording of actual Docker execution from the measured local suite; it is not a live run or an LLM trace.

- `repository_context.json`: submit this shape to `POST /v1/runs` after preparing a real sandbox.
- `action_request.json`: the supervisor's request to Madhav; one command and snapshot-bound patches.
- `execution_result.json`: Madhav's correlated response with updated candidate context.
- `verification_result.json`: independent checks and evidence manifest shape.
- `human_approval.json`: reply shape; replace approval ID with the current pending ID.
- `mock-replay.jsonl`: chronological mock trace for Likith's timeline development.

Regenerate with `uv run python examples/generate.py`. Production does not use mock providers
or automatic approval. Artifact references beginning `mock://` do not resolve to real files.

Example requests after configuring the API and setting `SUPERVISOR_SERVICE_TOKEN`:

```sh
curl -X POST http://127.0.0.1:8000/v1/runs \
  -H "Authorization: Bearer $SUPERVISOR_SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/repository_context.json
```

This request will only succeed end to end when the real configured execution service
recognizes the prepared snapshot; the sample snapshot is intentionally a mock identifier.
