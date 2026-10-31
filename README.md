# RepoRunner

Team Astroworld · Agentathon 2026 · Developer Productivity

RepoRunner aims to set up a GitHub repository inside a Docker sandbox, fix setup
errors, and verify the result with plain code.

Current status: project skeleton only. All Python modules contain docstrings;
the CLI, sandbox, verifier, UI, and evaluation harness are not implemented yet.

Requires Python 3.11+ and Docker for the planned sandbox.

Install the project and development dependencies:

```sh
python -m pip install -r requirements.txt
```

Planned run command (not functional yet):

```sh
python -m reporunner <repo_url>
```

Configure the model through `RR_MODEL` and the provider's API key environment
variable when the LLM wrapper is implemented. SQLite uses Python's standard
library; no separate database dependency is needed.

Follow the build order and verification gates in [AGENTS.md](AGENTS.md).
Run artifacts will live in the Git-ignored `runs/` directory.
