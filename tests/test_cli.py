"""Tests for the RepoRunner CLI.

Run with:  pytest tests/ -v

Uses Typer's CliRunner for command parsing and Rich Console capture for output.
Mock fixtures replace all backend calls so tests run without a live backend.
"""

from __future__ import annotations

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

# ── Make 'reporunner' importable from the project root ──────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from reporunner.cli import app
from reporunner.events import (
    EventKind,
    EventStage,
    RunEvent,
    VerifyStatus,
    WorkerStatus,
    append_event,
    load_events,
)
from reporunner.config import DEFAULT_CONFIG, load_config, save_config

runner = CliRunner()

# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory for every test."""
    import reporunner.config as cfg_mod
    import reporunner.cli as cli_mod

    cfg_dir  = tmp_path / ".reporunner"
    cfg_file = cfg_dir / "config.json"
    cfg_dir.mkdir(parents=True)
    cfg_data = DEFAULT_CONFIG.copy()
    cfg_data["mock_mode"] = True  # Safe default: never hit real backend
    with open(cfg_file, "w") as fh:
        json.dump(cfg_data, fh)

    monkeypatch.setattr(cfg_mod, "CONFIG_DIR",  cfg_dir)
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(cli_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setenv("RR_TEST", "1")  # suppress sleeps
    yield tmp_path


# ──────────────────────────────────────────────────────────────
# 1. Event model
# ──────────────────────────────────────────────────────────────


class TestRunEvent:
    def test_round_trip_json(self):
        ev = RunEvent(
            kind=EventKind.PLAN,
            text="Scanning repo",
            stage=EventStage.SUPERVISOR,
            run_id="run-1",
            detail={"tasks": 3},
        )
        ev2 = RunEvent.from_json(ev.to_json())
        assert ev2.kind  == EventKind.PLAN
        assert ev2.text  == "Scanning repo"
        assert ev2.stage == EventStage.SUPERVISOR
        assert ev2.run_id == "run-1"
        assert ev2.detail == {"tasks": 3}

    def test_from_dict_defaults(self):
        ev = RunEvent.from_dict({"kind": "error", "text": "boom"})
        assert ev.kind  == EventKind.ERROR
        assert ev.stage == EventStage.SYSTEM

    def test_all_event_kinds_valid(self):
        for kind in EventKind:
            ev = RunEvent(kind=kind, text="test")
            assert RunEvent.from_json(ev.to_json()).kind == kind

    def test_append_and_load_events(self, tmp_path):
        run_dir = tmp_path / "runs" / "run-x"
        ev1 = RunEvent(EventKind.PLAN, "plan 1", run_id="run-x")
        ev2 = RunEvent(EventKind.CMD,  "cmd 1",  run_id="run-x")
        append_event(run_dir, ev1)
        append_event(run_dir, ev2)
        loaded = load_events(run_dir)
        assert len(loaded) == 2
        assert loaded[0].kind == EventKind.PLAN
        assert loaded[1].kind == EventKind.CMD

    def test_load_events_missing_dir(self, tmp_path):
        assert load_events(tmp_path / "nonexistent") == []


# ──────────────────────────────────────────────────────────────
# 2. Configuration
# ──────────────────────────────────────────────────────────────


class TestConfig:
    def test_load_defaults(self):
        cfg = load_config()
        assert "backend_url" in cfg
        assert "mock_mode" in cfg

    def test_save_and_reload(self, tmp_path):
        import reporunner.config as cfg_mod
        new_data = DEFAULT_CONFIG.copy()
        new_data["backend_url"] = "http://test:9999"
        save_config(new_data)
        reloaded = load_config()
        assert reloaded["backend_url"] == "http://test:9999"

    def test_secrets_never_in_config(self, tmp_path):
        """update_config should not let callers silently write api_key to disk."""
        from reporunner.config import update_config
        update_config(backend_url="http://safe:8000")
        cfg = load_config()
        assert "api_key" not in cfg  # we never added it


# ──────────────────────────────────────────────────────────────
# 3. CLI parsing & help
# ──────────────────────────────────────────────────────────────


class TestHelp:
    def test_root_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "reporunner" in result.output.lower()

    def test_splash(self):
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        # Logo should be present (capital R characters)
        assert "REPORUNNER" in result.output.upper() or "RepoRunner" in result.output

    def test_init_help(self):
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0
        assert "--backend" in result.output

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--repo" in result.output
        assert "--task" in result.output

    def test_status_help(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0

    def test_logs_help(self):
        result = runner.invoke(app, ["logs", "--help"])
        assert result.exit_code == 0

    def test_workers_help(self):
        result = runner.invoke(app, ["workers", "--help"])
        assert result.exit_code == 0

    def test_verify_help(self):
        result = runner.invoke(app, ["verify", "--help"])
        assert result.exit_code == 0

    def test_approve_help(self):
        result = runner.invoke(app, ["approve", "--help"])
        assert result.exit_code == 0

    def test_reject_help(self):
        result = runner.invoke(app, ["reject", "--help"])
        assert result.exit_code == 0

    def test_replay_help(self):
        result = runner.invoke(app, ["replay", "--help"])
        assert result.exit_code == 0

    def test_benchmark_help(self):
        result = runner.invoke(app, ["benchmark", "--help"])
        assert result.exit_code == 0

    def test_config_help(self):
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0


# ──────────────────────────────────────────────────────────────
# 4. `reporunner init`
# ──────────────────────────────────────────────────────────────


class TestInit:
    def test_show_config(self):
        result = runner.invoke(app, ["init", "--show"])
        assert result.exit_code == 0
        assert "backend_url" in result.output

    def test_update_backend_url(self):
        with patch("reporunner.cli.request", side_effect=Exception("unreachable")):
            result = runner.invoke(app, ["init", "--backend", "http://newhost:9000"])
        assert result.exit_code == 0
        cfg = load_config()
        assert cfg["backend_url"] == "http://newhost:9000"

    def test_update_repo(self):
        result = runner.invoke(app, ["init", "--repo", "https://github.com/a/b"])
        assert result.exit_code == 0
        cfg = load_config()
        assert cfg["default_repo"] == "https://github.com/a/b"

    def test_enable_mock(self):
        result = runner.invoke(app, ["init", "--mock"])
        assert result.exit_code == 0
        cfg = load_config()
        assert cfg["mock_mode"] is True

    def test_disable_mock(self):
        result = runner.invoke(app, ["init", "--no-mock"])
        assert result.exit_code == 0
        cfg = load_config()
        assert cfg["mock_mode"] is False


# ──────────────────────────────────────────────────────────────
# 5. Repository validation
# ──────────────────────────────────────────────────────────────


class TestRepoValidation:
    def test_valid_github_https(self):
        from reporunner.cli import _valid_repo
        assert _valid_repo("https://github.com/psf/requests") is True

    def test_valid_github_ssh(self):
        from reporunner.cli import _valid_repo
        assert _valid_repo("git@github.com:psf/requests.git") is True

    def test_valid_local_path(self, tmp_path):
        from reporunner.cli import _valid_repo
        assert _valid_repo(str(tmp_path)) is True

    def test_invalid_random_string(self):
        from reporunner.cli import _valid_repo
        assert _valid_repo("not-a-url") is False

    def test_invalid_empty(self):
        from reporunner.cli import _valid_repo
        assert _valid_repo("") is False


# ──────────────────────────────────────────────────────────────
# 6. `reporunner run` — mock mode
# ──────────────────────────────────────────────────────────────


class TestRunCommand:
    def test_missing_repo_exits_nonzero(self):
        result = runner.invoke(app, ["run", "--task", "fix tests"])
        assert result.exit_code != 0

    def test_missing_task_exits_nonzero(self):
        result = runner.invoke(app, ["run", "--repo", "https://github.com/a/b"])
        assert result.exit_code != 0

    def test_invalid_repo_exits_nonzero(self):
        result = runner.invoke(
            app, ["run", "--repo", "not-a-repo", "--task", "do it", "--yes"]
        )
        assert result.exit_code != 0

    def test_mock_run_emits_events(self):
        result = runner.invoke(
            app,
            ["run", "--repo", "https://github.com/a/b", "--task", "Fix tests", "--yes"],
        )
        assert result.exit_code == 0
        assert "mock" in result.output.lower() or "MOCK" in result.output

    def test_mock_run_shows_plan_event(self):
        result = runner.invoke(
            app,
            ["run", "--repo", "https://github.com/a/b", "--task", "Fix tests", "--yes"],
        )
        assert result.exit_code == 0
        assert "SUPERVISOR" in result.output or "plan" in result.output.lower()

    def test_mock_run_plain_output(self):
        result = runner.invoke(
            app,
            [
                "run",
                "--repo", "https://github.com/a/b",
                "--task", "Fix tests",
                "--yes",
                "--plain",
            ],
        )
        assert result.exit_code == 0
        assert "SUPERVISOR" in result.output or "plan" in result.output.lower()


# ──────────────────────────────────────────────────────────────
# 7. `reporunner status`
# ──────────────────────────────────────────────────────────────


class TestStatus:
    def test_no_run_id_exits(self):
        result = runner.invoke(app, ["status"])
        assert result.exit_code != 0

    def test_mock_status(self):
        result = runner.invoke(app, ["status", "run-001"])
        assert result.exit_code == 0
        assert "run-001" in result.output

    def test_mock_status_plain(self):
        result = runner.invoke(app, ["status", "run-001", "--plain"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert "run_id" in parsed


# ──────────────────────────────────────────────────────────────
# 8. API client
# ──────────────────────────────────────────────────────────────


class TestApiClient:
    def test_request_raises_on_network_error(self):
        from reporunner.api_client import request
        with patch("reporunner.api_client._client") as mock_client:
            mock_ctx = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_client.return_value.__exit__  = MagicMock(return_value=False)
            mock_ctx.request.side_effect = Exception("connection refused")
            with pytest.raises(Exception, match="connection refused"):
                request("GET", "/health")

    def test_stream_yields_data_lines(self):
        from reporunner.api_client import stream
        fake_lines = [b"data: {\"kind\":\"plan\",\"text\":\"hi\"}"]
        with patch("reporunner.api_client._client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.iter_lines.return_value = iter(fake_lines)
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__  = MagicMock(return_value=False)
            mock_ctx = MagicMock()
            mock_ctx.stream.return_value = mock_resp
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_client.return_value.__exit__  = MagicMock(return_value=False)
            results = list(stream("/run/x/events"))
        assert results == ['{"kind":"plan","text":"hi"}']


# ──────────────────────────────────────────────────────────────
# 9. Event parsing
# ──────────────────────────────────────────────────────────────


class TestEventParsing:
    def test_parse_valid_event(self):
        raw = json.dumps({
            "kind": "plan", "text": "Planning…",
            "stage": "supervisor", "run_id": "r1",
        })
        ev = RunEvent.from_json(raw)
        assert ev.kind  == EventKind.PLAN
        assert ev.stage == EventStage.SUPERVISOR

    def test_parse_invalid_json_raises(self):
        with pytest.raises(Exception):
            RunEvent.from_json("{bad json")

    def test_parse_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            RunEvent.from_dict({"kind": "totally_unknown", "text": "x"})


# ──────────────────────────────────────────────────────────────
# 10. Workers
# ──────────────────────────────────────────────────────────────


class TestWorkers:
    def test_mock_workers(self):
        result = runner.invoke(app, ["workers", "run-001"])
        assert result.exit_code == 0
        assert "worker-1" in result.output or "COMPLETED" in result.output

    def test_mock_workers_plain(self):
        result = runner.invoke(app, ["workers", "run-001", "--plain"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert "workers" in parsed
        assert len(parsed["workers"]) > 0


# ──────────────────────────────────────────────────────────────
# 11. Verification
# ──────────────────────────────────────────────────────────────


class TestVerify:
    def test_mock_verify(self):
        result = runner.invoke(app, ["verify", "run-001"])
        assert result.exit_code == 0
        assert "GREEN" in result.output

    def test_mock_verify_plain(self):
        result = runner.invoke(app, ["verify", "run-001", "--plain"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["overall"] == "GREEN"

    def test_verify_status_enum_values(self):
        for v in ("GREEN", "AMBER", "RED"):
            assert VerifyStatus(v) is not None


# ──────────────────────────────────────────────────────────────
# 12. Approve
# ──────────────────────────────────────────────────────────────


class TestApprove:
    def test_mock_approve_confirmed(self):
        result = runner.invoke(app, ["approve", "run-001", "--yes"])
        assert result.exit_code == 0
        assert "approved" in result.output.lower() or "mock" in result.output.lower()

    def test_approve_shows_candidate_summary(self):
        result = runner.invoke(app, ["approve", "run-001", "--yes"])
        assert "Candidate" in result.output or "revision" in result.output.lower()

    def test_approve_aborted_on_no(self):
        result = runner.invoke(app, ["approve", "run-001"], input="n\n")
        assert result.exit_code == 0
        assert "cancel" in result.output.lower() or "abort" in result.output.lower()


# ──────────────────────────────────────────────────────────────
# 13. Reject
# ──────────────────────────────────────────────────────────────


class TestReject:
    def test_mock_reject_with_reason(self):
        result = runner.invoke(
            app, ["reject", "run-001", "--reason", "Dockerfile too large"]
        )
        assert result.exit_code == 0
        assert "rejected" in result.output.lower() or "mock" in result.output.lower()

    def test_reject_prompts_for_reason(self):
        result = runner.invoke(app, ["reject", "run-001"], input="Bad Dockerfile\n")
        assert result.exit_code == 0

    def test_reject_empty_reason_exits(self):
        result = runner.invoke(app, ["reject", "run-001", "--reason", ""])
        assert result.exit_code != 0


# ──────────────────────────────────────────────────────────────
# 14. Replay
# ──────────────────────────────────────────────────────────────


class TestReplay:
    def test_replay_local_events(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "runs" / "run-replay-1"
        ev1 = RunEvent(EventKind.PLAN,  "Planning",  EventStage.SUPERVISOR, "run-replay-1")
        ev2 = RunEvent(EventKind.GREEN, "Complete",  EventStage.VERIFIER,   "run-replay-1")
        append_event(run_dir, ev1)
        append_event(run_dir, ev2)

        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["replay", "run-replay-1", "--speed", "999"])
        assert result.exit_code == 0
        assert "SUPERVISOR" in result.output or "Planning" in result.output

    def test_replay_no_events_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["replay", "nonexistent-run"])
        assert result.exit_code != 0


# ──────────────────────────────────────────────────────────────
# 15. Benchmark
# ──────────────────────────────────────────────────────────────


class TestBenchmark:
    def test_no_results_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["benchmark"])
        assert result.exit_code == 0
        assert "results" in result.output.lower() or "No results" in result.output

    def test_reads_results_json(self, tmp_path, monkeypatch):
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        records = [
            {"repo": "https://github.com/a/b", "status": "GREEN",  "minutes": 3, "steps": 8, "failure_reason": None},
            {"repo": "https://github.com/c/d", "status": "RED",    "minutes": 9, "steps": 25, "failure_reason": "timeout"},
        ]
        with open(eval_dir / "results.json", "w") as fh:
            json.dump(records, fh)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["benchmark"])
        assert result.exit_code == 0
        assert "GREEN" in result.output
        assert "RED"   in result.output

    def test_benchmark_plain_output(self, tmp_path, monkeypatch):
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        records = [{"repo": "x/y", "status": "GREEN", "minutes": 1, "steps": 3}]
        with open(eval_dir / "results.json", "w") as fh:
            json.dump(records, fh)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["benchmark", "--plain"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert isinstance(parsed, list)


# ──────────────────────────────────────────────────────────────
# 16. Config command
# ──────────────────────────────────────────────────────────────


class TestConfigCommand:
    def test_shows_config(self):
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "backend_url" in result.output

    def test_plain_config(self):
        result = runner.invoke(app, ["config", "--plain"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert "backend_url" in parsed

    def test_no_secrets_in_output(self):
        result = runner.invoke(app, ["config", "--plain"])
        assert "api_key" not in result.output
        assert "password" not in result.output


# ──────────────────────────────────────────────────────────────
# 17. Missing backend (live mode)
# ──────────────────────────────────────────────────────────────


class TestMissingBackend:
    def _live_config(self, monkeypatch):
        """Switch config to live mode (no mock)."""
        from reporunner.config import load_config, save_config
        cfg = load_config()
        cfg["mock_mode"] = False
        cfg["backend_url"] = "http://localhost:19999"  # unreachable port
        save_config(cfg)

    def test_run_live_backend_unavailable(self, monkeypatch):
        self._live_config(monkeypatch)
        result = runner.invoke(
            app,
            [
                "run",
                "--repo", "https://github.com/a/b",
                "--task", "Fix tests",
                "--yes",
            ],
        )
        assert result.exit_code != 0
        # Should not produce success output
        assert "run started" not in result.output.lower()

    def test_status_live_backend_unavailable(self, monkeypatch):
        self._live_config(monkeypatch)
        result = runner.invoke(app, ["status", "run-fake-123"])
        assert result.exit_code != 0

    def test_logs_live_backend_unavailable(self, monkeypatch):
        self._live_config(monkeypatch)
        result = runner.invoke(app, ["logs", "run-fake-123"])
        assert result.exit_code != 0


# ──────────────────────────────────────────────────────────────
# 18. Plain-text mode across commands
# ──────────────────────────────────────────────────────────────


class TestPlainOutput:
    """All commands with --plain must produce parseable plain text."""

    def test_status_plain(self):
        result = runner.invoke(app, ["status", "run-001", "--plain"])
        assert result.exit_code == 0
        json.loads(result.output)  # must be valid JSON

    def test_workers_plain(self):
        result = runner.invoke(app, ["workers", "run-001", "--plain"])
        assert result.exit_code == 0
        json.loads(result.output)

    def test_verify_plain(self):
        result = runner.invoke(app, ["verify", "run-001", "--plain"])
        assert result.exit_code == 0
        json.loads(result.output)

    def test_config_plain(self):
        result = runner.invoke(app, ["config", "--plain"])
        assert result.exit_code == 0
        json.loads(result.output)
