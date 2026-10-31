# RepoRunner — Command Line Interface

RepoRunner is a sandboxed, multi-model repository execution engine. Given any GitHub repository, it works out how to run the project inside an isolated Docker sandbox, observes every error encountered, formulates minimal fixes, and retries until an independent check passes (e.g. the repository's test suite or an HTTP health check).

The CLI is the user's primary interface to the RepoRunner execution engine.

---

## 1. Installation

### Requirements
- **Python 3.11+**
- **Docker** (for local sandbox execution)
- Terminal with ANSI color support (colorless / plain-text fallbacks included)

### Install dependencies

```bash
git clone https://github.com/InterstellarMC/reporunner.git
cd reporunner
git checkout feat/likith-cli

python -m pip install -e .[dev]
```

Or install dependencies directly:

```bash
python -m pip install typer rich httpx pytest
```

---

## 2. Quick Start

Run the CLI splash screen:

```bash
python -m reporunner
# or using the console script:
reporunner
```

Configure your backend URL:

```bash
reporunner init --backend http://localhost:8000
```

Submit a repository setup task:

```bash
reporunner run --repo https://github.com/pallets/flask --task "Install dependencies and run tests"
```

Or launch interactive mode:

```bash
reporunner run -i
```

---

## 3. Command Reference

| Command | Syntax | Description |
|---|---|---|
| `reporunner` | `reporunner` | Splash screen with ASCII branding, version, backend status, and quick-start |
| `init` | `reporunner init [OPTIONS]` | Configure backend URL, default repository, default branch, or mock mode |
| `run` | `reporunner run [OPTIONS]` | Submit a run; streams live supervisor, worker, aggregator, and sandbox events |
| `status` | `reporunner status [RUN_ID]` | Inspect current status, active workers, latest sandbox result, and verification |
| `logs` | `reporunner logs <RUN_ID> [OPTIONS]` | View execution logs with filters (`--stage`, `--worker`, `--level`, `--follow`) |
| `workers` | `reporunner workers <RUN_ID>` | Display specialist worker assignments, models, status, and patch references |
| `verify` | `reporunner verify <RUN_ID>` | Show independent verification results (`GREEN`, `AMBER`, `RED`) |
| `approve` | `reporunner approve <RUN_ID> [OPTIONS]`| Inspect candidate summary and apply to target branch upon explicit confirmation |
| `reject` | `reporunner reject <RUN_ID> --reason <TEXT>`| Reject candidate revision with a recorded audit reason |
| `replay` | `reporunner replay <RUN_ID> [--speed N]`| Replay saved execution trace in chronological order |
| `benchmark` | `reporunner benchmark [--file PATH]`| Display benchmark results from evaluation runs (`eval/results.json`) |
| `config` | `reporunner config [--plain]` | Display and validate current non-secret configuration |

All commands support `--help` and `--plain` (where applicable) for automation and CI/CD pipelines.

---

## 4. Configuration Reference

RepoRunner stores non-secret configuration locally in `~/.reporunner/config.json`. Credentials and secrets are never committed or saved to this file.

```json
{
  "backend_url": "http://localhost:8000",
  "default_repo": "https://github.com/example/repo",
  "default_task": "Run tests",
  "default_branch": "main",
  "mock_mode": false
}
```

### Configuration Commands

```bash
# View configuration and test backend connectivity
reporunner config

# Set backend endpoint
reporunner init --backend http://127.0.0.1:8000

# Set default repository
reporunner init --repo https://github.com/pallets/flask

# Enable offline mock mode (for CLI development / UI demos without backend)
reporunner init --mock

# Disable mock mode
reporunner init --no-mock
```

---

## 5. Live Execution Interface

During `reporunner run`, the CLI streams structured events via SSE:

1. **Run Header**: Displays Run ID, repository URL, task summary, elapsed time, and status.
2. **Supervisor**: Shows task decomposition and number of parallel sub-tasks created.
3. **Specialist Workers**: Shows live worker status (`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `BLOCKED`) with assigned models and results.
4. **Aggregator**: Displays received proposals, accepted patches, and conflict resolutions.
5. **Sandbox**: Live command execution inside Docker, stdout/stderr tail, duration, and exit codes.
6. **Refinement**: Error diagnosis and targeted follow-up tasks.
7. **Verifier**: Independent checks (pytest, HTTP 200, entrypoint exit code) and fresh-container reproducibility.

---

## 6. Example Workflows

### Standard End-to-End Run
```bash
# 1. Start execution
reporunner run --repo https://github.com/psf/requests --task "Run test suite"

# 2. Check worker status
reporunner workers run-20261101-001

# 3. Check independent verification
reporunner verify run-20261101-001

# 4. Review candidate and approve
reporunner approve run-20261101-001
```

### Debugging and Log Inspection
```bash
# Follow logs for sandbox stage only
reporunner logs run-20261101-001 --stage sandbox --follow

# Filter logs by errors only
reporunner logs run-20261101-001 --level error
```

### Replaying Past Runs
```bash
# Replay local execution trace at 2x speed
reporunner replay run-20261101-001 --speed 2
```

---

## 7. Backend Communication

The CLI interacts with the RepoRunner backend through a standard REST + Server-Sent Events (SSE) contract:

- `GET /health`: Connectivity check
- `POST /run`: Initiate a new run (`{"repo": "...", "task": "...", "target_branch": "..."}`)
- `GET /run/{id}/status`: Poll execution status
- `GET /run/{id}/events`: SSE stream of live execution events
- `GET /run/{id}/logs`: Retrieve stage/worker logs
- `GET /run/{id}/workers`: List specialist workers and status
- `GET /run/{id}/verify`: Independent verification results
- `GET /run/{id}/candidate`: Candidate summary (patches, warnings)
- `POST /run/{id}/approve`: Approve and commit candidate
- `POST /run/{id}/reject`: Reject candidate with recorded reason

### Offline / Mock Mode
When the backend is unavailable or when developing CLI layouts, enable mock mode:
```bash
reporunner init --mock
```
In mock mode, the CLI simulates realistic multi-model event streams without making external HTTP requests.

---

## 8. Troubleshooting

| Issue | Cause | Resolution |
|---|---|---|
| `Backend unavailable: [WinError 10061]` | Backend server is not running at configured URL | Check if the backend is running (`uvicorn app:app` or similar) or run `reporunner init --mock` to simulate. |
| `Invalid repository URL` | Provided path does not exist or URL is malformed | Ensure URL starts with `https://github.com/` or specify an existing local directory. |
| `Approving a RED candidate is not allowed` | Verification checks failed | Review failures via `reporunner verify <id>` or reject with `reporunner reject <id> --reason ...`. |
| Terminal character encoding errors | Non-UTF8 legacy Windows console | The CLI automatically configures UTF-8 encoding on Windows standard streams. |

---

## 9. Running Tests

Run the full pytest test suite (71 unit tests covering all commands, event parsing, validation, and plain-text output):

```bash
python -m pytest tests/test_cli.py -v
```
