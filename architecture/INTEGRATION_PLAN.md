# Integration and operation

The integrated dashboard uses `backend.app.server:create_application` and the local Docker
adapter. Start it with `uv run --extra screenshots reporunner serve`. All modules are present;
there is no separate frontend build or service required for local use.

## Local UI API

Opening `/` establishes the launch-specific HTTP-only SameSite session. All `/api` calls
require it; mutations also require `X-RepoRunner: 1`. Host and Origin must be local/same-origin.

| Endpoint | Purpose |
| --- | --- |
| GET /api/health | Actual Docker health and configured model state |
| GET /api/fixtures | Curated real broken examples |
| GET, POST /api/runs | History or submit URL/fixture, local/live mode, opt-in network |
| GET /api/runs/{id} | Status, metadata, artifact links |
| DELETE /api/runs/{id} | Cancel and request sandbox cleanup |
| GET /api/runs/{id}/events?after=N | Ordered new events |
| GET /api/runs/{id}/replay | Export recorded JSONL |
| POST /api/runs/{id}/approval | Matching approval ID and boolean decision |
| POST /api/runs/{id}/resources | Provision memory-only resource; return opaque reference |
| GET /api/runs/{id}/artifacts/{filename} | Authorized evidence download |
| GET, PUT /api/settings | Non-secret model settings / replace in-memory providers |
| GET, POST /api/benchmarks | Recorded local evaluations / launch real fixture evaluation |

Normal runs require interactive final review. Only the explicitly invoked known-fixture
evaluator applies automatic test-policy approval. It cannot apply that policy to a submitted
public repository. Missing capabilities become approval pauses, denials, and honest reports.

## Optional split-service deployment

The earlier bearer-token supervisor API remains at `backend.orchestrator.api:app_factory`.
Its HttpExecutionAdapter expects POST /v1/execute, /v1/verify, and /v1/close with the generated
contracts. That service split is optional; the integrated local app does not need it.
Schemas are draft v1 contracts ready for team review, not externally approved agreements.

## Before the event

1. Install dependencies and pre-pull both official runtime images. Check Docker health.
2. Configure the actual supervisor and open-source worker endpoints locally; do not put
   their keys in the repository. Run Live AI on a representative real repository.
3. Re-run all tests and the ten-fixture Docker evaluation. Preserve both failures and passes.
4. Test final approval, resource provisioning, denial, cancellation, and replay in the browser.
5. Rehearse docs/DEMO.md. Clearly distinguish live runs, saved real recordings, local rules,
   and live models. Do not imply that the local benchmark measures arbitrary GitHub success.
6. Push this project to the team's chosen repository and review before release. No remote
   or team checkout was supplied, so this delivery itself has no GitHub push or merged PR.

Known limits: one local process; interrupted runs stop on restart; fixed operator-selected
runtime images; conventional Python/Node test layouts; HTTP probe port 8000; no GPU/private
registry provisioning, public hosting, distributed queue, or generalized production isolation.
