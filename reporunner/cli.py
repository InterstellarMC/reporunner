"""RepoRunner CLI – full command implementation.

Technology choices
──────────────────
• Python 3.11+ (existing project language)
• Typer  >= 0.9   (existing dependency; declarative commands, auto-help)
• Rich   >= 13    (existing dependency; tables, panels, live display, progress)
• httpx  >= 0.27  (existing dependency; sync + streaming HTTP)

No new frameworks are added.  All commands live here and import only from
sibling modules.  The app.py Streamlit UI is untouched.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Reconfigure stdout/stderr to UTF-8 with error replacement to prevent cp1252 crash on Windows
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import typer
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.status import Status
from rich.table import Table
from rich.text import Text

try:
    from . import __version__
except (ImportError, AttributeError):
    __version__ = "0.1.0"
from .config import load_config, save_config, update_config, CONFIG_FILE
from .api_client import request, stream
from .events import EventKind, EventStage, RunEvent, VerifyStatus, WorkerStatus, load_events

# ──────────────────────────────────────────────────────────────
# App & console
# ──────────────────────────────────────────────────────────────

app = typer.Typer(
    name="reporunner",
    add_completion=False,
    no_args_is_help=False,
    help="RepoRunner — multi-model repository execution engine.",
    rich_markup_mode="rich",
)

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

# ──────────────────────────────────────────────────────────────
# Color / style helpers
# ──────────────────────────────────────────────────────────────

ORANGE = "bold #FF5F1F"
GREEN  = "bold #7ED957"
YELLOW = "bold yellow"
RED    = "bold red"
GRAY   = "dim"
WHITE  = "bold white"


def _ok(msg: str) -> None:
    console.print(f"[{GREEN}]✓[/] {msg}")


def _warn(msg: str) -> None:
    console.print(f"[{YELLOW}]⚠[/] {msg}")


def _err(msg: str) -> None:
    err_console.print(f"[{RED}]✗[/] {msg}")


def _info(msg: str) -> None:
    console.print(f"[{GRAY}]{msg}[/]")


def _mock_banner(plain: bool = False) -> None:
    if plain:
        return
    console.print(
        Panel(
            "[bold yellow]MOCK MODE — no real backend calls are being made[/]\n"
            "Run [cyan]reporunner init --backend <url>[/] to connect to a real backend.",
            title="[yellow]⚠ Mock Mode[/]",
            border_style="yellow",
        )
    )


# ──────────────────────────────────────────────────────────────
# ASCII logo / splash
# ──────────────────────────────────────────────────────────────

LOGO = r"""
 ____  _____ ____   ___  ____  _   _ _   _ _   _ _____ ____
|  _ \| ____|  _ \ / _ \|  _ \| | | | \ | | \ | | ____|  _ \
| |_) |  _| | |_) | | | | |_) | | | |  \| |  \| |  _| | |_) |
|  _ <| |___|  __/| |_| |  _ <| |_| | |\  | |\  | |___|  _ <
|_| \_\_____|_|    \___/|_| \_\\___/|_| \_|_| \_|_____|_| \_\
"""


def _check_backend(cfg: dict) -> tuple[bool, str]:
    """Return (connected, message) by pinging the backend."""
    if cfg.get("mock_mode"):
        return False, "mock mode"
    try:
        base = cfg.get("backend_url", "http://localhost:8000")
        resp = request("GET", "/health")
        return True, base
    except Exception as exc:
        return False, str(exc)


# ──────────────────────────────────────────────────────────────
# Default command: splash screen
# ──────────────────────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Show the RepoRunner splash screen when no sub-command is given."""
    if ctx.invoked_subcommand is not None:
        return

    cfg = load_config()
    connected, conn_msg = _check_backend(cfg)

    console.print(f"[{ORANGE}]{LOGO}[/]")
    console.print(
        f"  [bold]RepoRunner[/]  [dim]v{__version__}[/]  "
        "— sandboxed, multi-model repository execution engine\n",
        highlight=False,
    )

    # Status grid
    status_table = Table(box=None, show_header=False, padding=(0, 2))
    status_table.add_column(style="dim", width=22)
    status_table.add_column()

    backend_str = (
        f"[{GREEN}]● connected[/]  {conn_msg}"
        if connected
        else (
            f"[{YELLOW}]● mock mode[/]"
            if cfg.get("mock_mode")
            else f"[{RED}]● unavailable[/]  {conn_msg}"
        )
    )
    status_table.add_row("Backend", backend_str)
    status_table.add_row(
        "Config",
        f"[cyan]{CONFIG_FILE}[/]",
    )
    repo_ctx = cfg.get("default_repo") or "[dim](none)[/]"
    status_table.add_row("Default repo", repo_ctx)

    console.print(status_table)
    console.print()

    # Commands table
    cmds = Table(box=box.SIMPLE, show_header=True, header_style=ORANGE)
    cmds.add_column("Command", style="cyan", min_width=20)
    cmds.add_column("Description")
    rows = [
        ("reporunner init",      "Configure backend URL and project defaults"),
        ("reporunner run",       "Submit a repository + task to the execution engine"),
        ("reporunner status",    "Show run status"),
        ("reporunner logs",      "Stream or display execution logs"),
        ("reporunner workers",   "List specialist worker details"),
        ("reporunner verify",    "Display verification results"),
        ("reporunner approve",   "Approve a candidate and apply it"),
        ("reporunner reject",    "Reject a candidate with a recorded reason"),
        ("reporunner replay",    "Replay a saved execution trace"),
        ("reporunner benchmark", "Display benchmark / eval results"),
        ("reporunner config",    "Show and validate current configuration"),
    ]
    for cmd, desc in rows:
        cmds.add_row(cmd, desc)
    console.print(cmds)

    # Quick-start
    console.print(Rule(style="dim"))
    console.print("[bold]Quick start[/]\n")
    console.print("  [cyan]reporunner init --backend http://localhost:8000[/]")
    console.print(
        "  [cyan]reporunner run --repo https://github.com/example/project "
        '--task "Fix the failing tests"[/]'
    )
    console.print()
    console.print(
        "  Run [cyan]reporunner <command> --help[/] for detailed usage.",
        highlight=False,
    )
    console.print()


# ──────────────────────────────────────────────────────────────
# init
# ──────────────────────────────────────────────────────────────

@app.command()
def init(
    backend: Optional[str] = typer.Option(
        None, "--backend", help="Backend service URL (e.g. http://localhost:8000)."
    ),
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Default GitHub URL or local path."
    ),
    branch: Optional[str] = typer.Option(
        None, "--branch", help="Default target branch."
    ),
    mock: Optional[bool] = typer.Option(
        None, "--mock/--no-mock", help="Enable/disable mock mode."
    ),
    show: bool = typer.Option(
        False, "--show", help="Print current configuration and exit."
    ),
) -> None:
    """Configure RepoRunner.

    Stores non-secret configuration in [cyan]~/.reporunner/config.json[/].
    Credentials are never written to the config file or to the repository.
    """
    cfg = load_config()

    if show or (
        backend is None and repo is None and branch is None and mock is None
    ):
        _show_config(cfg)
        return

    changes: dict = {}
    if backend is not None:
        url = backend.rstrip("/")
        changes["backend_url"] = url
        # Validate
        try:
            request("GET", "/health")
            _ok(f"Backend reachable at {url}")
        except Exception as exc:
            _warn(f"Backend not reachable ({exc}). URL saved — try again later.")
    if repo is not None:
        changes["default_repo"] = repo
    if branch is not None:
        changes["default_branch"] = branch
    if mock is not None:
        changes["mock_mode"] = mock
        if mock:
            _warn("Mock mode enabled — all commands will simulate responses.")
        else:
            _ok("Mock mode disabled — live backend will be used.")

    if changes:
        update_config(**changes)
        _ok(f"Configuration saved to {CONFIG_FILE}")
    else:
        _show_config(cfg)


def _show_config(cfg: dict) -> None:
    """Pretty-print non-secret configuration."""
    table = Table(title="RepoRunner Configuration", box=box.ROUNDED, border_style="dim")
    table.add_column("Key", style="cyan", min_width=22)
    table.add_column("Value")
    SECRET_KEYS = {"api_key", "token", "secret", "password", "credential"}
    for k, v in cfg.items():
        if any(s in k.lower() for s in SECRET_KEYS):
            table.add_row(k, "[dim]<hidden>[/]")
        else:
            table.add_row(k, str(v))
    console.print(table)
    console.print(f"[dim]Config file: {CONFIG_FILE}[/]")


# ──────────────────────────────────────────────────────────────
# run
# ──────────────────────────────────────────────────────────────

@app.command()
def run(
    repo: Optional[str] = typer.Option(
        None, "--repo", help="GitHub URL or local repository path."
    ),
    task: Optional[str] = typer.Option(
        None, "--task", help="Task description (e.g. 'Fix the failing tests')."
    ),
    branch: Optional[str] = typer.Option(
        None, "--branch", help="Target branch for the candidate output."
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Interactive prompt mode."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt."
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Plain-text output (for CI / non-interactive pipes)."
    ),
) -> None:
    """Submit a repository + task to the RepoRunner execution engine.

    Examples:

      reporunner run --repo https://github.com/example/project --task "Fix tests"

      reporunner run -i
    """
    cfg = load_config()

    # ── Collect inputs ────────────────────────────────────────
    if interactive:
        repo = repo or typer.prompt("Repository (GitHub URL or local path)")
        task = task or typer.prompt("Task description")
        branch = branch or typer.prompt(
            "Target branch", default=cfg.get("default_branch", "main")
        )
    else:
        if not repo:
            _err("--repo is required (or use -i for interactive mode).")
            raise typer.Exit(1)
        if not task:
            _err("--task is required (or use -i for interactive mode).")
            raise typer.Exit(1)
        branch = branch or cfg.get("default_branch", "main")

    # ── Validate ──────────────────────────────────────────────
    if not _valid_repo(repo):
        _err(
            f"Invalid repository: {repo!r}\n"
            "  Provide a GitHub URL (https://github.com/owner/repo) "
            "or an existing local path."
        )
        raise typer.Exit(1)

    if len(task.strip()) < 5:
        _err("Task description is too short. Please be more specific.")
        raise typer.Exit(1)

    # ── Confirmation ──────────────────────────────────────────
    if not plain:
        console.print()
        console.print(
            Panel(
                f"[bold]Repository:[/]  {escape(repo)}\n"
                f"[bold]Task:[/]        {escape(task)}\n"
                f"[bold]Branch:[/]      {escape(branch)}\n\n"
                "[dim]RepoRunner will create a candidate rather than modifying "
                "the original repository directly.[/]",
                title="[bold]Execution configuration[/]",
                border_style=ORANGE,
            )
        )

    if not yes and not plain:
        confirmed = typer.confirm("Start this run?")
        if not confirmed:
            _info("Aborted.")
            raise typer.Exit()

    # ── Mock mode ─────────────────────────────────────────────
    if cfg.get("mock_mode"):
        _mock_banner(plain)
        _run_mock(repo, task, branch, plain)
        return

    # ── Submit to backend ─────────────────────────────────────
    payload = {"repo": repo, "task": task, "target_branch": branch}
    try:
        with Status("Submitting run to backend…", console=console):
            resp = request("POST", "/run", json_body=payload)
    except Exception as exc:
        _err(f"Failed to start run: {exc}")
        _info("Is the backend running? Check: reporunner config")
        raise typer.Exit(1)

    run_id = resp.get("run_id")
    if not run_id:
        _err("Backend did not return a run_id. Check backend logs.")
        raise typer.Exit(1)

    _ok(f"Run submitted — ID: [bold]{run_id}[/]")
    _info(f"Streaming live events from /run/{run_id}/events …")
    _info("Press Ctrl+C to stop following (the run continues in the backend).\n")

    _stream_live_events(run_id, plain=plain)


def _valid_repo(repo: str) -> bool:
    if not repo or not repo.strip():
        return False
    if repo.startswith("https://github.com/") or repo.startswith("git@github.com:"):
        return True
    p = Path(repo)
    if p.exists() and p.is_dir():
        return True
    return False


def _run_mock(repo: str, task: str, branch: str, plain: bool) -> None:
    """Emit a realistic mock event stream so the CLI can be developed offline."""
    from .events import EventKind, EventStage, RunEvent

    mock_run_id = "mock-run-001"
    events = [
        RunEvent(EventKind.PLAN,    "Scanning repository…",                 EventStage.SUPERVISOR, mock_run_id),
        RunEvent(EventKind.PLAN,    "Decomposing task into 3 sub-tasks",    EventStage.SUPERVISOR, mock_run_id,
                 detail={"tasks_created": 3}),
        RunEvent(EventKind.WORKER,  "worker-1 RUNNING: Analyse dependencies", EventStage.WORKER,  mock_run_id,
                 detail={"worker_id": "worker-1", "status": "RUNNING", "model": "gpt-4o-mini"}),
        RunEvent(EventKind.WORKER,  "worker-2 RUNNING: Draft Dockerfile",   EventStage.WORKER,    mock_run_id,
                 detail={"worker_id": "worker-2", "status": "RUNNING", "model": "deepseek-coder"}),
        RunEvent(EventKind.WORKER,  "worker-1 COMPLETED",                   EventStage.WORKER,    mock_run_id,
                 detail={"worker_id": "worker-1", "status": "COMPLETED"}),
        RunEvent(EventKind.AGG,     "Aggregator: 2 proposals received, 2 accepted", EventStage.AGGREGATOR, mock_run_id),
        RunEvent(EventKind.SANDBOX, "Sandbox: running `pip install -r requirements.txt`", EventStage.SANDBOX, mock_run_id),
        RunEvent(EventKind.ERROR,   "Error: No module named 'distutils'",   EventStage.SANDBOX,   mock_run_id),
        RunEvent(EventKind.FIX,     "Fix: switching to python:3.10-slim",   EventStage.SUPERVISOR, mock_run_id),
        RunEvent(EventKind.VERIFY,  "Verifier: running pytest …",           EventStage.VERIFIER,  mock_run_id),
        RunEvent(EventKind.GREEN,   "✓ GREEN — tests pass, run complete",   EventStage.VERIFIER,  mock_run_id,
                 detail={"status": "GREEN", "checks_passed": 5, "checks_failed": 0}),
    ]

    _print_run_header(mock_run_id, repo, task, plain=plain)

    for ev in events:
        _render_event(ev, plain=plain)


# ──────────────────────────────────────────────────────────────
# Live event streaming helpers
# ──────────────────────────────────────────────────────────────

_STAGE_COLORS = {
    EventStage.SUPERVISOR:  "cyan",
    EventStage.WORKER:      "blue",
    EventStage.AGGREGATOR:  "magenta",
    EventStage.SANDBOX:     "yellow",
    EventStage.VERIFIER:    GREEN,
    EventStage.REFINEMENT:  "orange1",
    EventStage.SYSTEM:      "dim",
}

_KIND_SYMBOLS = {
    EventKind.PLAN:    "◆",
    EventKind.CMD:     "▶",
    EventKind.ERROR:   "✗",
    EventKind.FIX:     "⟳",
    EventKind.ASK:     "?",
    EventKind.VERIFY:  "⏺",
    EventKind.GREEN:   "✓",
    EventKind.STOPPED: "■",
    EventKind.WORKER:  "⚙",
    EventKind.AGG:     "⊕",
    EventKind.SANDBOX: "▣",
    EventKind.REFINE:  "↻",
    EventKind.STATUS:  "ℹ",
}


def _render_event(event: RunEvent, *, plain: bool = False) -> None:
    symbol = _KIND_SYMBOLS.get(event.kind, "·")
    stage  = event.stage.value.upper() if event.stage else "SYS"
    color  = _STAGE_COLORS.get(event.stage, "dim")
    ts     = event.t[11:19] if event.t and len(event.t) >= 19 else ""

    if plain:
        print(f"[{ts}] [{stage}] {symbol} {event.text}")
    else:
        # Color-coded line
        line = Text()
        line.append(f" {ts} ", style="dim")
        line.append(f" {stage:<12}", style=color)
        if event.kind == EventKind.GREEN:
            line.append(f" {symbol} ", style=GREEN)
        elif event.kind == EventKind.ERROR:
            line.append(f" {symbol} ", style=RED)
        else:
            line.append(f" {symbol} ", style=color)
        line.append(event.text)
        console.print(line)

        # Optional detail block
        if event.detail and isinstance(event.detail, dict):
            detail_text = json.dumps(event.detail, indent=2)
            for dl in detail_text.splitlines():
                console.print(f"         [dim]{escape(dl)}[/]")


def _print_run_header(run_id: str, repo: str, task: str, *, plain: bool = False) -> None:
    if plain:
        print(f"Run ID: {run_id}")
        print(f"Repo:   {repo}")
        print(f"Task:   {task}")
        print("-" * 60)
        return
    console.print(
        Panel(
            f"[bold]Run ID:[/]  {escape(run_id)}\n"
            f"[bold]Repo:[/]    {escape(repo)}\n"
            f"[bold]Task:[/]    {escape(task)}",
            title=f"[{ORANGE}]● RepoRunner Live Execution[/]",
            border_style=ORANGE,
        )
    )
    console.print()


def _stream_live_events(run_id: str, *, plain: bool = False) -> None:
    seen: set[str] = set()
    try:
        for raw in stream(f"/run/{run_id}/events"):
            # Deduplicate
            if raw in seen:
                continue
            seen.add(raw)

            try:
                event = RunEvent.from_json(raw)
            except Exception:
                _info(f"SSE: {raw}")
                continue

            _render_event(event, plain=plain)

            if event.kind in (EventKind.GREEN, EventKind.STOPPED):
                break
    except KeyboardInterrupt:
        _warn("\nFollowing stopped (run continues in the backend).")
        _info(f"  reporunner status {run_id}")
    except Exception as exc:
        _err(f"Stream error: {exc}")
        _info(f"  reporunner status {run_id}  to poll state manually.")


# ──────────────────────────────────────────────────────────────
# status
# ──────────────────────────────────────────────────────────────

@app.command()
def status(
    run_id: Optional[str] = typer.Argument(None, help="Run identifier. Omit for the latest run."),
    plain: bool = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Show status of a run.

    Example:

      reporunner status run-abc123
    """
    cfg = load_config()
    run_id = run_id or cfg.get("last_run_id")
    if not run_id:
        _err("No run ID given and no last run found. Pass a run ID.")
        raise typer.Exit(1)

    if cfg.get("mock_mode"):
        _mock_banner(plain)
        _print_mock_status(run_id, plain)
        return

    try:
        data = request("GET", f"/run/{run_id}/status")
    except Exception as exc:
        _err(f"Could not fetch status: {exc}")
        raise typer.Exit(1)

    if plain:
        print(json.dumps(data, indent=2))
        return

    _render_status_panel(run_id, data)


def _print_mock_status(run_id: str, plain: bool) -> None:
    data = {
        "run_id":           run_id,
        "repository":       "https://github.com/example/project",
        "status":           "RUNNING",
        "cycle":            2,
        "active_workers":   2,
        "latest_sandbox":   "exit_code=1, stderr: ModuleNotFoundError",
        "verification":     "PENDING",
    }
    if plain:
        print(json.dumps(data, indent=2))
    else:
        _render_status_panel(run_id, data)


def _render_status_panel(run_id: str, data: dict) -> None:
    status_val  = data.get("status", "UNKNOWN")
    verify_val  = data.get("verification", "PENDING")

    status_color = {
        "RUNNING":   "cyan",
        "COMPLETED": GREEN,
        "FAILED":    RED,
        "PENDING":   YELLOW,
    }.get(status_val, "dim")

    verify_color = {
        "GREEN": GREEN, "AMBER": YELLOW, "RED": RED, "PENDING": "dim"
    }.get(verify_val, "dim")

    table = Table(box=box.ROUNDED, border_style="dim", show_header=False)
    table.add_column(style="dim", min_width=22)
    table.add_column()
    table.add_row("Run ID",          f"[bold]{escape(run_id)}[/]")
    table.add_row("Repository",      escape(str(data.get("repository", ""))))
    table.add_row("Status",          f"[{status_color}]{escape(status_val)}[/]")
    table.add_row("Cycle",           str(data.get("cycle", "—")))
    table.add_row("Active workers",  str(data.get("active_workers", "—")))
    table.add_row("Latest sandbox",  escape(str(data.get("latest_sandbox", "—"))))
    table.add_row("Verification",    f"[{verify_color}]{escape(verify_val)}[/]")

    console.print(Panel(table, title=f"[bold]Run status: {run_id}[/]", border_style="dim"))


# ──────────────────────────────────────────────────────────────
# logs
# ──────────────────────────────────────────────────────────────

@app.command()
def logs(
    run_id: str = typer.Argument(..., help="Run identifier."),
    worker: Optional[str] = typer.Option(None, "--worker", help="Filter by worker ID."),
    stage: Optional[str]  = typer.Option(None, "--stage",  help="Filter by stage (supervisor/worker/sandbox/…)."),
    level: Optional[str]  = typer.Option(None, "--level",  help="Filter by level (error/warn/info)."),
    follow: bool          = typer.Option(False, "--follow", "-f", help="Follow live log stream."),
    plain: bool           = typer.Option(False, "--plain",  help="Plain-text output."),
) -> None:
    """Display execution logs for a run.

    Examples:

      reporunner logs run-abc123

      reporunner logs run-abc123 --stage sandbox --follow

      reporunner logs run-abc123 --level error
    """
    cfg = load_config()

    # Try local JSONL first (always available if run was local)
    runs_dir = Path("runs") / run_id
    if runs_dir.exists():
        events = load_events(runs_dir)
        events = _filter_events(events, worker=worker, stage=stage, level=level)
        for ev in events:
            _render_event(ev, plain=plain)
        if not follow:
            return

    if cfg.get("mock_mode"):
        _mock_banner(plain)
        _info(f"[Mock] Would GET /run/{run_id}/logs")
        return

    params: dict = {}
    if worker:
        params["worker"] = worker
    if stage:
        params["stage"] = stage
    if level:
        params["level"] = level

    if follow:
        _info("Following live log stream. Press Ctrl+C to stop.\n")
        try:
            for raw in stream(f"/run/{run_id}/logs", params=params):
                try:
                    ev = RunEvent.from_json(raw)
                    _render_event(ev, plain=plain)
                except Exception:
                    _info(raw)
        except KeyboardInterrupt:
            _warn("Log following stopped.")
        return

    try:
        data = request("GET", f"/run/{run_id}/logs", params=params)
        raw_logs = data.get("logs", "")
        if plain:
            print(raw_logs)
        else:
            console.print(raw_logs)
    except Exception as exc:
        _err(f"Could not fetch logs: {exc}")
        raise typer.Exit(1)


def _filter_events(
    events: list[RunEvent],
    *,
    worker: Optional[str],
    stage: Optional[str],
    level: Optional[str],
) -> list[RunEvent]:
    out = events
    if worker:
        out = [
            e for e in out
            if isinstance(e.detail, dict) and e.detail.get("worker_id") == worker
        ]
    if stage:
        out = [e for e in out if e.stage.value == stage.lower()]
    if level == "error":
        out = [e for e in out if e.kind == EventKind.ERROR]
    elif level == "warn":
        out = [e for e in out if e.kind in (EventKind.ERROR, EventKind.STOPPED)]
    return out


# ──────────────────────────────────────────────────────────────
# workers
# ──────────────────────────────────────────────────────────────

@app.command()
def workers(
    run_id: str  = typer.Argument(..., help="Run identifier."),
    plain: bool  = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """List specialist worker details for a run.

    Example:

      reporunner workers run-abc123
    """
    cfg = load_config()

    if cfg.get("mock_mode"):
        _mock_banner(plain)
        data = _mock_workers_data()
    else:
        try:
            data = request("GET", f"/run/{run_id}/workers")
        except Exception as exc:
            _err(f"Could not fetch workers: {exc}")
            raise typer.Exit(1)

    worker_list = data.get("workers", [])
    if plain:
        print(json.dumps(data, indent=2))
        return

    table = Table(
        title=f"Workers — run {run_id}",
        box=box.ROUNDED,
        border_style="dim",
        show_lines=True,
    )
    table.add_column("Worker ID", style="bold cyan", min_width=12)
    table.add_column("Model",     min_width=16)
    table.add_column("Task",      min_width=24)
    table.add_column("Status",    min_width=12)
    table.add_column("Result / Patch")

    STATUS_STYLE = {
        "QUEUED":    "dim",
        "RUNNING":   "cyan",
        "COMPLETED": GREEN,
        "FAILED":    RED,
        "BLOCKED":   YELLOW,
    }

    for w in worker_list:
        stat = w.get("status", "UNKNOWN")
        table.add_row(
            escape(w.get("worker_id", "")),
            escape(w.get("model", "")),
            escape(w.get("task", "")),
            f"[{STATUS_STYLE.get(stat, 'dim')}]{escape(stat)}[/]",
            escape(str(w.get("result") or w.get("patch_ref") or "—")),
        )

    console.print(table)


def _mock_workers_data() -> dict:
    return {
        "workers": [
            {"worker_id": "worker-1", "model": "gpt-4o-mini",    "task": "Analyse dependencies",
             "status": "COMPLETED", "result": "requirements detected: flask, gunicorn"},
            {"worker_id": "worker-2", "model": "deepseek-coder",  "task": "Draft Dockerfile",
             "status": "COMPLETED", "patch_ref": "patch-0001"},
            {"worker_id": "worker-3", "model": "qwen-2.5-coder",  "task": "Fix import error",
             "status": "RUNNING",   "result": None},
        ]
    }


# ──────────────────────────────────────────────────────────────
# verify
# ──────────────────────────────────────────────────────────────

@app.command()
def verify(
    run_id: str  = typer.Argument(..., help="Run identifier."),
    plain: bool  = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Display verification results for a run.

    Verification status is determined by the independent verifier (plain code,
    no model confidence). GREEN/AMBER/RED only.

    Example:

      reporunner verify run-abc123
    """
    cfg = load_config()

    if cfg.get("mock_mode"):
        _mock_banner(plain)
        data = _mock_verify_data()
    else:
        try:
            data = request("GET", f"/run/{run_id}/verify")
        except Exception as exc:
            _err(f"Could not fetch verification: {exc}")
            raise typer.Exit(1)

    if plain:
        print(json.dumps(data, indent=2))
        return

    _render_verify(run_id, data)


def _mock_verify_data() -> dict:
    return {
        "overall":       "GREEN",
        "reproducible":  True,
        "checks": [
            {"name": "pytest",            "status": "PASSED"},
            {"name": "http_200",          "status": "PASSED"},
            {"name": "entrypoint_exit_0", "status": "PASSED"},
            {"name": "fresh_container",   "status": "PASSED"},
        ],
    }


def _render_verify(run_id: str, data: dict) -> None:
    overall = data.get("overall", "UNKNOWN")
    color = {VerifyStatus.GREEN: GREEN, VerifyStatus.AMBER: YELLOW, VerifyStatus.RED: RED}.get(
        VerifyStatus(overall) if overall in VerifyStatus._value2member_map_ else None,
        "dim",
    )

    checks = data.get("checks", [])
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("Check",   min_width=24)
    table.add_column("Status",  min_width=10)
    CSTYLE = {"PASSED": GREEN, "FAILED": RED, "SKIPPED": YELLOW}
    for ch in checks:
        st = ch.get("status", "UNKNOWN")
        table.add_row(
            escape(ch.get("name", "")),
            f"[{CSTYLE.get(st, 'dim')}]{escape(st)}[/]",
        )

    reproducible = data.get("reproducible")
    repro_str = (
        f"[{GREEN}]✓ Yes[/]" if reproducible
        else f"[{RED}]✗ No[/]" if reproducible is False
        else "[dim]—[/]"
    )

    console.print(
        Panel(
            table,
            title=f"[{color}]● Verification: {overall}[/]  — run {run_id}",
            subtitle=f"Fresh-container reproducibility: {repro_str}",
            border_style=color if overall in ("GREEN", "AMBER", "RED") else "dim",
        )
    )


# ──────────────────────────────────────────────────────────────
# approve
# ──────────────────────────────────────────────────────────────

@app.command()
def approve(
    run_id: str  = typer.Argument(..., help="Run identifier to approve."),
    yes: bool    = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    plain: bool  = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Approve a candidate and apply it to the target branch.

    The candidate summary (patches, verification, warnings) is shown before
    any changes are applied.  Confirmation is required.

    Example:

      reporunner approve run-abc123
    """
    cfg = load_config()

    if cfg.get("mock_mode"):
        _mock_banner(plain)
        data = _mock_candidate_data(run_id)
    else:
        try:
            data = request("GET", f"/run/{run_id}/candidate")
        except Exception as exc:
            _err(f"Could not fetch candidate: {exc}")
            raise typer.Exit(1)

    # Always display summary first
    _render_candidate_summary(run_id, data, plain)

    # Gate on verification
    overall = data.get("verification", {}).get("overall", "UNKNOWN")
    if overall == "RED":
        _err(
            "Verification status is RED. Approving a RED candidate is not allowed.\n"
            "  Fix failures and re-run, or use: reporunner reject "
            f"{run_id} --reason 'explain here'"
        )
        raise typer.Exit(1)
    if overall == "AMBER":
        _warn("Verification status is AMBER. Review outstanding warnings above.")

    if not yes:
        confirmed = typer.confirm(
            f"\nApply candidate to [{data.get('target_branch', 'main')}]?",
            default=False,
        )
        if not confirmed:
            _info("Approval cancelled. No changes were made.")
            raise typer.Exit()

    if cfg.get("mock_mode"):
        _ok(f"[Mock] Candidate {run_id} approved.")
        return

    try:
        resp = request("POST", f"/run/{run_id}/approve")
        _ok(
            f"Candidate approved. Commit: {resp.get('commit', '—')}  "
            f"Branch: {resp.get('branch', '—')}"
        )
    except Exception as exc:
        _err(f"Approval failed: {exc}")
        raise typer.Exit(1)


def _mock_candidate_data(run_id: str) -> dict:
    return {
        "run_id":        run_id,
        "revision":      "cand-rev-42",
        "patches":       ["patch-0001 (Dockerfile)", "patch-0002 (requirements.txt)"],
        "verification":  {"overall": "GREEN", "checks_passed": 4, "checks_failed": 0},
        "warnings":      [],
        "target_branch": "main",
    }


def _render_candidate_summary(run_id: str, data: dict, plain: bool) -> None:
    patches  = data.get("patches", [])
    warnings = data.get("warnings", [])
    verify   = data.get("verification", {})
    overall  = verify.get("overall", "UNKNOWN")
    color    = {VerifyStatus.GREEN: GREEN, VerifyStatus.AMBER: YELLOW, VerifyStatus.RED: RED}.get(
        VerifyStatus(overall) if overall in VerifyStatus._value2member_map_ else None, "dim"
    )

    if plain:
        print(f"Candidate:    {data.get('revision', '—')}")
        print(f"Patches:      {', '.join(patches) or 'none'}")
        print(f"Verification: {overall}")
        print(f"Warnings:     {', '.join(warnings) or 'none'}")
        print(f"Target:       {data.get('target_branch', '—')}")
        return

    lines = [
        f"[bold]Candidate revision:[/]  {escape(data.get('revision', '—'))}",
        f"[bold]Target branch:[/]       {escape(data.get('target_branch', '—'))}",
        f"[bold]Verification:[/]        [{color}]{escape(overall)}[/]  "
        f"({verify.get('checks_passed', 0)} passed, {verify.get('checks_failed', 0)} failed)",
        "",
        f"[bold]Accepted patches[/] ({len(patches)}):",
    ]
    for p in patches:
        lines.append(f"  • {escape(p)}")

    if warnings:
        lines.append("")
        lines.append(f"[{YELLOW}][bold]Warnings[/] ({len(warnings)}):[/]")
        for w in warnings:
            lines.append(f"  [{YELLOW}]⚠[/] {escape(w)}")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]Candidate summary[/]",
            border_style=color,
        )
    )


# ──────────────────────────────────────────────────────────────
# reject
# ──────────────────────────────────────────────────────────────

@app.command()
def reject(
    run_id: str              = typer.Argument(..., help="Run identifier to reject."),
    reason: Optional[str]    = typer.Option(None, "--reason", help="Reason for rejection."),
    plain: bool              = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Reject a candidate with a recorded reason.

    Example:

      reporunner reject run-abc123 --reason "Tests pass but Dockerfile is overly large"
    """
    cfg = load_config()
    reason = reason or typer.prompt("Reason for rejection")
    if not reason.strip():
        _err("A reason is required to reject a candidate.")
        raise typer.Exit(1)

    if cfg.get("mock_mode"):
        _mock_banner(plain)
        _ok(f"[Mock] Candidate {run_id} rejected: {reason}")
        return

    try:
        request("POST", f"/run/{run_id}/reject", json_body={"reason": reason})
        _ok(f"Candidate {run_id} rejected. Reason recorded.")
    except Exception as exc:
        _err(f"Rejection failed: {exc}")
        raise typer.Exit(1)


# ──────────────────────────────────────────────────────────────
# replay
# ──────────────────────────────────────────────────────────────

@app.command()
def replay(
    run_id: str  = typer.Argument(..., help="Run identifier to replay."),
    speed: float = typer.Option(1.0, "--speed", help="Playback speed multiplier (1.0 = real-time)."),
    plain: bool  = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Replay a saved execution trace in chronological order.

    Reads from local runs/<id>/events.jsonl. Pass --speed 2 for 2× faster.

    Example:

      reporunner replay run-abc123 --speed 2
    """
    runs_dir = Path("runs") / run_id
    events = load_events(runs_dir)

    if not events:
        # Try fetching from backend
        cfg = load_config()
        if cfg.get("mock_mode"):
            _mock_banner(plain)
            _err(f"No events found for run {run_id}.")
            raise typer.Exit(1)
        try:
            data = request("GET", f"/run/{run_id}/events/all")
            raw_list = data.get("events", [])
            events = [RunEvent.from_dict(e) for e in raw_list]
        except Exception as exc:
            _err(f"No local events found and backend unavailable: {exc}")
            raise typer.Exit(1)

    if not events:
        _err(f"No events found for run {run_id}.")
        raise typer.Exit(1)

    if not plain:
        console.print(
            Panel(
                f"Replaying [bold]{len(events)}[/] events for run [cyan]{run_id}[/]  "
                f"(speed ×{speed})",
                title="[bold]Replay[/]",
                border_style=ORANGE,
            )
        )
        console.print()

    # Replay with timing
    prev_time: Optional[datetime] = None
    for ev in events:
        try:
            ev_time = datetime.fromisoformat(ev.t.replace("Z", "+00:00"))
        except Exception:
            ev_time = None

        if prev_time and ev_time and not os.environ.get("RR_TEST"):
            delta = (ev_time - prev_time).total_seconds()
            if 0 < delta < 10:  # cap gap at 10s
                time.sleep(delta / speed)

        _render_event(ev, plain=plain)
        prev_time = ev_time

    if not plain:
        console.print()
        _ok("Replay complete.")


# ──────────────────────────────────────────────────────────────
# benchmark
# ──────────────────────────────────────────────────────────────

@app.command()
def benchmark(
    results_file: Optional[str] = typer.Option(
        None, "--file", "-f",
        help="Path to results JSON file (default: eval/results.json).",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Display benchmark / evaluation results.

    Results are read from eval/results.json produced by eval/run_eval.py.
    No results are invented.

    Example:

      reporunner benchmark

      reporunner benchmark --file eval/results.json
    """
    path = Path(results_file) if results_file else Path("eval") / "results.json"

    if not path.exists():
        _warn(
            f"No results file found at {path}.\n"
            "  Run: python eval/run_eval.py\n"
            "  to generate evaluation results."
        )
        return

    try:
        with open(path, encoding="utf-8") as fh:
            records = json.load(fh)
    except Exception as exc:
        _err(f"Could not read {path}: {exc}")
        raise typer.Exit(1)

    if plain:
        print(json.dumps(records, indent=2))
        return

    table = Table(
        title=f"Benchmark results  [dim]({path})[/]",
        box=box.ROUNDED,
        border_style="dim",
        show_lines=True,
    )
    table.add_column("Repository",  min_width=32)
    table.add_column("Status",      min_width=8)
    table.add_column("Minutes",     min_width=8)
    table.add_column("Steps",       min_width=6)
    table.add_column("Failure reason")

    STATUS_STYLE = {
        "GREEN": GREEN,
        "AMBER": YELLOW,
        "RED":   RED,
    }

    for rec in records:
        stat = rec.get("status", "UNKNOWN")
        table.add_row(
            escape(rec.get("repo", "")),
            f"[{STATUS_STYLE.get(stat, 'dim')}]{escape(stat)}[/]",
            str(rec.get("minutes", "—")),
            str(rec.get("steps", "—")),
            escape(str(rec.get("failure_reason") or "—")),
        )

    console.print(table)
    passed  = sum(1 for r in records if r.get("status") == "GREEN")
    amber   = sum(1 for r in records if r.get("status") == "AMBER")
    failed  = sum(1 for r in records if r.get("status") == "RED")
    console.print(
        f"\n  [{GREEN}]GREEN {passed}[/]   [{YELLOW}]AMBER {amber}[/]   [{RED}]RED {failed}[/]"
        f"   out of {len(records)} repos\n"
    )


# ──────────────────────────────────────────────────────────────
# config
# ──────────────────────────────────────────────────────────────

@app.command()
def config(
    plain: bool = typer.Option(False, "--plain", help="Plain-text output."),
) -> None:
    """Show and validate the current non-secret configuration.

    Secret values (API keys, tokens) are never displayed.

    Example:

      reporunner config
    """
    cfg = load_config()

    if plain:
        safe = {
            k: v for k, v in cfg.items()
            if not any(s in k.lower() for s in ("api_key", "token", "secret", "password"))
        }
        print(json.dumps(safe, indent=2))
        return

    _show_config(cfg)

    # Connectivity check
    console.print()
    if cfg.get("mock_mode"):
        _warn("Mock mode is active — backend is not contacted.")
        return

    with Status("Checking backend connectivity…", console=console):
        connected, msg = _check_backend(cfg)

    if connected:
        _ok(f"Backend reachable: {msg}")
    else:
        _warn(
            f"Backend not reachable: {msg}\n"
            "  Set a different URL: reporunner init --backend <url>"
        )
