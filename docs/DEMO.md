# A three-minute demo with auditable evidence

Start Docker Desktop, run `uv run --extra screenshots reporunner serve`, and open
`http://127.0.0.1:8765`. Pre-pull `python:3.11-slim` and `node:20-slim` before the event.
Use a configured high-capability supervisor and an open-source worker endpoint for Live AI.
If no endpoints are available, select Local recipes and explicitly explain that it is a
deterministic engine using the same real sandbox and verifier. Do not present it as an LLM.

## 0:00–0:25 — The problem

“A README is a claim. We want proof. RepoRunner reads a repository, tries to run it,
uses the actual errors to guide repairs, and returns an independently checked candidate.”

## 0:25–1:20 — Fail, observe, repair

Choose **Missing dependency**. Show the scoped worker tasks and actual ModuleNotFoundError.
The example ships a real wheel but forgets to install it; there is no Internet dependency.
Show the small requirements patch and actual successful installation. Original tests are
protected and remain unchanged. The verifier repeats them independently.

## 1:20–1:50 — It cannot grade itself

Show the fresh image build, independent rerun, current snapshot identity, and downloadable
Dockerfile, RUN.md, logs, and patch. Before accepting the final candidate, inspect Evidence
and Changes. Only the final approval turns the verified candidate GREEN.

## 1:50–2:20 — Ask instead of invent

Choose **Missing secret**. The actual test throws a missing-key error; RepoRunner pauses.
Stop and report the blocker, or provision a clearly labelled demonstration value for
`DEMO_API_KEY`. That fixture validates presence only; it does not call a real API or
validate a real provider credential. Provisioned values are not sent to model prompts.

## 2:20–3:00 — The receipts

Show the complete local benchmark, including the three blocked cases, and open a row's
evidence. State the scope: ten curated local repositories, not ten arbitrary public repos.
Replay one exported timeline with the network disconnected. A second dependency run can
show memory reducing model/planning actions while still performing verification and review.

For an HTTP demonstration, run **Broken web app** ahead of the presentation and inspect its
recorded trace. The screenshot is a rendering of the actual HTTP response with JavaScript
and external requests disabled. The saved trace is a recording of real execution, not a
live run; label it as replay when using it. Budget about a minute for a fresh HTTP run.

## Answers judges can verify

- “How do you know it worked?” Open the original test output and fresh-container rerun.
- “Can the model cheat?” It cannot set GREEN; original test-file patches are rejected,
  commands run in an unprivileged sandbox, and fresh verification starts from host-tracked source.
- “What if it loops?” Show action/time caps and repeat-error strategy changes and stopping.
- “How does it handle secrets?” Show the approval pause, memory-only resource reference,
  and logs with known supplied values redacted.
- “Where are the agents?” Live AI uses separate supervisor and worker endpoints, structured
  scoped tasks, parallel execution, aggregation, and runtime feedback. Local recipes are labelled separately.

Do not claim a competition result, an unmeasured general repair rate, or live model quality
from the deterministic fixture benchmark.
