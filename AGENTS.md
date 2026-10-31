# AGENTS.md: RepoRunner (Agentathon 2026, team Astroworld)

Read this fully before writing any code. Follow the build order. Do not add scope.

## 1. What we are building
RepoRunner: given a GitHub repo URL, an LLM agent works out how to run the project inside a Docker sandbox, reads every error it hits, fixes it, and retries until an INDEPENDENT check passes (the repo's own tests, or the app answering HTTP 200). It outputs a working `Dockerfile`, a `RUN.md` (one command to run it) and an `evidence/` folder (log, test result, screenshot). If it cannot finish, it outputs a clear report of what blocked it.

Judging is on: innovation, technical depth, usability, presentation. A focused, working agent beats an ambitious one that does not run. The final round needs a WORKING PROTOTYPE and a live demo. Reliability of the demo is the top priority.

## 2. Non-negotiable rules
1. The LLM can never declare success. Only the verifier (plain code, no LLM) can end a run as GREEN.
2. Every command from the agent runs inside the Docker sandbox. Never on the host. No host mounts except a throwaway workdir. Non-root user, CPU/RAM/time caps.
3. Hard caps per run: 25 steps, 10 minutes wall clock, a token budget. On cap: stop and write a report.
4. If the same error signature appears twice: force a strategy change (different base image, pinned version, different install route). A third time: stop and report.
5. Missing secret, API key, GPU or private package: pause and ask the human one clear question (`ask_human`). Never invent credentials.
6. Do not claim anything in the UI or docs that was not produced by a real run. No fake results, no hardcoded success.
7. Keep it small. No auth, no database server, no microservices, no extra frameworks. If a feature is not in section 9, do not build it.

## 3. Stack (keep exactly this)
- Python 3.11+, one package `reporunner/`.
- LLM via LiteLLM (provider-agnostic; model name from env `RR_MODEL`, key from env). Use tool/function calling.
- Docker SDK for Python (`docker`) for the sandbox.
- SQLite (`sqlite3`, stdlib) for recipe memory.
- Streamlit for the UI. A CLI (`python -m reporunner <repo_url>`) for dev and as demo fallback.
- Playwright (Python) for the screenshot of the running app. pytest for our own tests.
- Agent loop: a plain Python loop is fine and preferred (easier to debug in 24h). LangGraph only if it saves time, not otherwise.

## 4. Architecture

```
reporunner/
  agent.py        # the loop (plan -> act -> observe/fix -> verify)
  llm.py          # LiteLLM wrapper, retries, token accounting
  tools.py        # tool schemas + implementations
  sandbox.py      # Docker container lifecycle, exec with timeout, output truncation
  verifier.py     # independent checks (NO LLM)
  memory.py       # SQLite recipes: error signature -> fix that worked
  report.py       # writes Dockerfile, RUN.md, evidence/, failure report
  events.py       # structured events streamed to UI/CLI
  cli.py
app.py            # Streamlit UI
eval/
  repos.json      # 10 test repos: 7 fixable, 3 deliberately blocked
  run_eval.py     # runs all, writes results.md + results.json
```

### Run flow
1. `scan`: clone repo (shallow) into the sandbox workdir. Collect: file tree (depth 2), README (first 200 lines), manifests (`requirements*.txt`, `pyproject.toml`, `setup.py`, `package.json`, lockfiles, `Dockerfile`, `docker-compose.yml`, `Makefile`, `.github/workflows/*`, `.python-version`, `.nvmrc`). Detect stack (Python / Node first; anything else: report "unsupported" cleanly).
2. `plan` (LLM): choose base image, install commands, run command, and the CHECK (see verifier). Look up memory first.
3. `act`: execute ONE command in the sandbox via `run_cmd`.
4. `observe/fix` (LLM): given the last command, exit code, trimmed stdout/stderr (last ~120 lines), and history summary, pick the likeliest cause and ONE fix (a command or a file edit).
5. Loop 3-4. Router (plain code) checks caps, repeated errors, secret/GPU patterns before each step.
6. When the run command succeeds or the plan says it is ready: call the verifier. Fail: feed verifier output back into step 4.
7. GREEN: write artifacts, save every successful fix as a recipe. Otherwise write the failure report.

### Tools exposed to the LLM (function calling)
- `run_cmd(cmd: str, timeout_s: int=120)` -> {exit_code, stdout_tail, stderr_tail}
- `read_file(path: str, max_lines: int=200)`
- `write_file(path: str, content: str)`
- `list_dir(path: str)`
- `ask_human(question: str, options: list[str])` -> blocks until UI/CLI answers
- `finish(summary: str)` -> only REQUESTS verification; it does not end the run.

### Verifier (plain code)
Pick in order, as determined at plan time and recorded in the run state:
1. Repo's own tests: `pytest -x -q` / `npm test` (timeout 180s). Pass = exit 0.
2. App check: start the entrypoint in background in the sandbox, poll the port for HTTP 200 (timeout 60s), grab a Playwright screenshot of `/`.
3. Entrypoint exits 0 within the timeout (for CLI tools/scripts).
Record which check was used and its raw output in `evidence/`.

### Recipe memory (SQLite)
Table `recipes(id, error_signature, stack, fix_description, fix_commands, success_count, last_used)`. Error signature = normalised key lines (strip paths, line numbers, versions). Before the LLM guesses, look up by signature and inject matching recipes into the prompt as hints. Save a recipe only after a run goes GREEN. Seed with 5 to 10 well-known fixes (e.g. `No module named 'distutils'` on Python 3.12 -> use python:3.10/3.11; `libGL.so.1` -> `apt-get install -y libgl1 libglib2.0-0`; `pg_config not found` -> `libpq-dev` or `psycopg2-binary`; Node `ERR_OSSL_EVP_UNSUPPORTED` -> `NODE_OPTIONS=--openssl-legacy-provider` or Node 16/18).

### Events (drive both UI and CLI)
Emit a structured event for every step: `{t, kind: plan|cmd|error|fix|ask|verify|green|stopped, text, detail}`. The UI renders these as the live timeline. Build this FIRST; it is the demo.

## 5. System prompt for the agent (starting point)
- You are an environment-setup engineer working in a Linux Docker sandbox. Goal: get this repo running and verified.
- Work one command at a time. After each result, state the likeliest cause in one sentence, then make ONE change.
- Prefer minimal fixes: base image/version choice, system packages, pinned dependencies, env vars with safe defaults. Do not rewrite the project's source code unless there is no alternative; if you must, say why.
- Never fabricate secrets. If a secret/credential/GPU is needed, call `ask_human`.
- Do not repeat a failing command unchanged. If a fix did not work, try a different approach.
- You cannot declare success. Call `finish` when you believe the check should pass; the system verifies independently.
- Output the final Dockerfile content via `write_file` to `/work/Dockerfile.generated` so it is reproducible from a clean image.

## 6. Reproducibility requirement
The final `Dockerfile` must build from scratch and pass the same check. After GREEN, the system rebuilds from the generated Dockerfile in a fresh container and re-runs the check. Only then mark GREEN. (This is the strongest proof of trust; do it if time allows after the loop works, before UI polish.)

## 7. UI requirements (Streamlit), match the wireframe slide
- Input: repo URL + Run button. Sidebar: model, step cap, time cap.
- Live timeline: icons for plan / retry-fix / ask / verify; show the error text and the agent's one-line decision; timestamps.
- Ask-a-human banner with buttons (use local SQLite / paste secret / stop) when `ask_human` fires.
- Proof panel: GREEN or STOPPED badge, elapsed time, download buttons for Dockerfile, RUN.md, evidence zip, screenshot thumbnail.
- Dark theme, orange (#FF5F1F) and green (#7ED957) accents to match the deck.
- A "Replay" mode that plays back a saved events JSON of a past successful run. THIS IS OUR DEMO FALLBACK if the network or Docker misbehaves.

## 8. Evaluation harness (drives our claims; never fake it)
`eval/repos.json`: 10 public repos chosen NOW and fixed: 7 that should be fixable (a mix of Python/Flask, Django, FastAPI, a Node/Express app, one OpenCV project, one that needs a local DB fallback), and 3 deliberately blocked (needs a paid API key, needs GPU, needs a private package). `run_eval.py` runs each with the caps, saves per-run events, and writes `results.md`: repo, GREEN/AMBER(asked human)/RED, minutes, steps, cost, failure reason. Show this table in the final pitch, failures included. Report honestly; do not tune claims.

## 9. Build order (stop at each gate and verify before moving on)
1. `sandbox.py` + `run_cmd`: start container, exec command, timeout, output truncation, cleanup. Test with a trivial repo.
2. `events.py` + CLI streaming output.
3. `agent.py` loop with `run_cmd`, `read_file`, `write_file`, `list_dir` and the caps. GATE: one simple Python repo reaches the verifier.
4. `verifier.py` (tests, HTTP, exit 0) + `finish`. GATE: GREEN on that repo, end to end, from the CLI.
5. Repeat-error detection, strategy change, `ask_human`, failure report.
6. `memory.py` + seed recipes + hint injection.
7. `report.py`: Dockerfile, RUN.md, evidence, screenshot.
8. Streamlit UI + replay mode.
9. `eval/run_eval.py` on the 10 repos. Fix the top 3 failure causes only.
10. Reproducibility rebuild check (section 6).
Stretch, ONLY if everything above is done and stable: batch CSV mode. Nothing else.

## 10. How Codex should work in this repo
- Make small commits after every gate. Never leave `main` broken; keep a tag `demo-ok` on the last commit where the full demo worked.
- Before saying a step is done: run it for real (not just tests that mock everything) and paste the actual output.
- Write a few pytest tests for pure logic only (error-signature normaliser, router rules, recipe lookup). Do not spend time on exhaustive tests.
- Log everything to `runs/<id>/events.jsonl`. Every demo run is saved so we can replay it.
- Handle failures defensively: Docker not running, network timeouts, LLM errors (retry with backoff, 3 tries), huge outputs (truncate to the tail), zombie containers (always remove in `finally`).
- Prefer boring, readable code. No premature abstraction, no new dependencies without a reason.
- When unsure about a requirement, choose the simplest option that keeps the demo reliable and note it in `NOTES.md`.
- Pre-pull images before the demo: `python:3.10-slim`, `python:3.12-slim`, `node:18-slim`, `node:20-slim`.

## 11. Demo definition of done (what must work, live, in 3 minutes)
1. Paste a broken-by-default repo URL (one we rehearsed).
2. Timeline shows a plan, at least one real error, and a fix chosen with a one-line reason.
3. One `ask_human` moment resolved with a button click.
4. Independent verification goes GREEN; Dockerfile, RUN.md and screenshot appear.
5. Show the eval table (10 repos, honest results) and one RED/AMBER case that stopped cleanly instead of guessing.
6. Replay mode ready as a fallback; a recorded video as a second fallback.

## 12. Facts for the pitch (all from the deck; do not exaggerate)
- 74% of 9,078 research R files failed on first re-run (Trisovic et al., Scientific Data 2022); ~24% of ~864k GitHub notebooks ran without errors (Pimentel et al., MSR 2019).
- Research loops (Repo2Run, 2025) set up 86% of 420 Python repos; another benchmark (SetupBench, 2025) saw 39 to 57% on repo setup. We are honest: the approach is proven in research, our contribution is a usable tool with an independent verifier, recipe memory, auditable evidence and a human-in-the-loop for secrets.
- Do not present estimates (cost, time per repo) as measurements; use the numbers from our own eval run.

---

# Appendix: full project context (everything decided so far)

## A. Event and team
- Event: Agentathon 2026, 24-hour state-level hackathon by Encode.AI (AIML dept), PES University RR Campus, Bengaluru. Started 9:00 a.m. 31 Oct, ends 1 Nov. Prize pool 36,000 rupees. Teams of 3 to 4. Venue Wi-Fi is shared and may be slow; bring laptops and chargers. AI tooling and cloud resources are provided by the organisers (details unknown; keep the LLM layer provider-agnostic so sponsor credits drop in via config).
- Our team: Astroworld (3 people, all PESU RR Campus). Mudit Chaturvedi (leader: agent loop, integration, owns `main`), Madhav (sandbox, verifier, test repos), Likith (UI, CLI, demo, pitch deck).
- We were shortlisted (top 35 teams) on the strength of a 10-slide-plus PPT. The final round is a working prototype plus a 10 to 12 slide deck and a pitch to judges.
- Judging criteria: innovation, technical depth, usability, presentation. The template's own focus: problem clarity, agentic design, real-world impact, and what was actually shipped in 24 hours. "A focused, working agent beats an ambitious idea that doesn't run." Goal: finish 1st.
- Track: Developer Productivity.

## B. What our submitted deck promised (the prototype must match it)
Slides: team; read-this-first (organiser's); problem; solution; wireframes + tech stack; data flow; one illustrative run; feasibility + impact; 24-hour plan; references.
- Product: RepoRunner. One line: "give it any GitHub repo; it figures out setup, fixes its own errors, and returns a running project." Agentic role: "an LLM agent runs commands in a sandbox, reads each error, fixes it, and retries until an independent check passes."
- Persona: TAs and judges who must run dozens of repos they didn't write (35 teams at this hackathon), plus interns on day one.
- Existing alternatives we named: README + pasting errors into ChatGPT; Codespaces/dev containers (need a devcontainer.json, else a generic image); docker init / Nixpacks / repo2docker (generate or build from detected config, once).
- Solution points: plan, run one command, read output, one fix, repeat; done only when an independent check passes; same error twice forces a new strategy; hard caps 25 steps and 10 minutes; asks a human for secrets/GPU/private packages; memory of past fixes.
- Data flow stages: 1 INPUT, 2 PLAN (LLM, reads files), 3 ACT (run_cmd, write_file), 4 READ + FIX (LLM, memory read), 5 VERIFY (plain code). Side paths: ASK A HUMAN or STOP + REPORT; MEMORY (SQLite recipes); OUTPUT (Dockerfile, RUN.md, evidence card with screenshot).
- Wireframe (match it): URL field + Run button; live timeline (plan, retry-fix icons, ask, verify); proof panel (GREEN badge, time, Dockerfile, RUN.md, evidence card, screenshot thumbnail); amber "Paused: needs DATABASE_URL" banner with buttons "Use local SQLite", "Paste secret", "Stop"; tab "Batch (CSV), stretch".
- Illustrative run in the deck (our demo should reproduce this kind of story with real repos): README plan -> `No module named 'distutils'` on Python 3.12 -> retry on python:3.10-slim -> `libGL.so.1` missing -> apt install libgl1 libglib2.0-0 -> `KeyError: 'DATABASE_URL'` -> ask human, use local SQLite -> pytest passes + HTTP 200 -> GREEN.
- 24-hour plan promised: sandbox first, then the loop with 5 tools and independent verifier, then robustness (loop detection, caps, ask-human, memory, Dockerfile + RUN.md), then UI/CLI and a 10-repo eval (7 fixable, 3 deliberately blocked), code freeze, rehearse. Batch mode is a stretch goal only.
- Estimates we showed (must be replaced by measured numbers from our eval before the final pitch): under about 50 rupees and under 10 minutes per repo; 10 minutes per repo manual setup assumption (about 6 hours for 35 repos).

## C. Honesty rules (we lose credibility if broken)
- Never present estimates as measurements. Final deck and pitch must use our own eval numbers, failures included.
- We are not claiming to have invented the loop approach. Prior art: Repo2Run (86% on 420 Python repos), SetupBench (39 to 57% on repo setup), EnvBench (best baseline 6.7% of Python repos). Our contribution: a usable tool with an independent verifier, recipe memory, auditable evidence, and human-in-the-loop for secrets, aimed at evaluators.
- No hardcoded or faked results anywhere, including the replay mode (replays must be of real saved runs).
- Code must be written during the hackathon.

## D. Priorities if time runs short (cut from the bottom)
1. Must: end-to-end GREEN on a real repo from the CLI with the independent verifier and sandbox.
2. Must: live timeline UI + proof panel + replay mode.
3. Must: eval table on the 10 repos with honest results.
4. Should: ask_human flow, repeat-error strategy change, recipe memory, reproducibility rebuild.
5. Could: screenshot, Node support depth.
6. Only if everything is stable: batch CSV mode.
Code freeze around 05:00 on 1 Nov; the last hours are for rehearsal, a recorded backup video, and updating the deck with real results. Do not start new features after freeze.
