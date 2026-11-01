import json
import sqlite3
from datetime import datetime, timezone
from .models import RunEvent, RunView


class Store:
    def __init__(self, path="reporunner.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(run_id TEXT, sequence INTEGER, body TEXT,
          PRIMARY KEY(run_id, sequence));
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, body TEXT);
        CREATE TABLE IF NOT EXISTS recipes(context_key TEXT, error_key TEXT, body TEXT,
          PRIMARY KEY(context_key, error_key));
        """)

    def save(self, view: RunView):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?)", (view.run_id, view.model_dump_json()))

    def view(self, run_id):
        row = self.db.execute("SELECT body FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunView.model_validate_json(row[0])

    def emit(self, run_id, event_type, **data):
        with self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            event = RunEvent(run_id=run_id, sequence=seq, timestamp=datetime.now(timezone.utc).isoformat(), type=event_type, data=data)
            self.db.execute("INSERT INTO events VALUES (?,?,?)", (run_id, seq, event.model_dump_json()))
        return event

    def events(self, run_id, after=0):
        return [RunEvent.model_validate_json(row[0]) for row in self.db.execute(
            "SELECT body FROM events WHERE run_id=? AND sequence>? ORDER BY sequence", (run_id, after))]

    def remember(self, context_key, error_key, plan):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (context_key, error_key, json.dumps(plan)))

    def recall(self, context_key, error_key):
        row = self.db.execute("SELECT body FROM recipes WHERE context_key=? AND error_key=?", (context_key, error_key)).fetchone()
        return json.loads(row[0]) if row else None

    def interrupt_orphans(self):
        # Replays survive restarts; active model/sandbox sessions do not silently resume.
        for row in self.db.execute("SELECT body FROM runs").fetchall():
            view = RunView.model_validate_json(row[0])
            if view.status in ("running", "awaiting_approval"):
                view.status, view.reason, view.pending_approval = "stopped", "service restarted; submit a new run", None
                view.approval_kind = None
                self.save(view)
                self.emit(view.run_id, "run.stopped", reason=view.reason)
