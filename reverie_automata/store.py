"""Durable state — the agent's continuity organ.

One sqlite database holds cycles, the live task ledger, the single work queue
(``threads``), the artifact-bound approval log, falsifiable lessons, and raw
learning traces. Everything the agent knows across cycles lives here; a cycle is
ephemeral compute over this persistent mind.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
  ts TEXT PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL,
  status TEXT NOT NULL DEFAULT 'running', grade TEXT, cost REAL, plan_json TEXT
);
CREATE TABLE IF NOT EXISTS tasks (                 -- the LIVE ledger (rows as work happens)
  id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_ts TEXT NOT NULL, task_id TEXT NOT NULL,
  what TEXT, mode TEXT, risk TEXT, status TEXT NOT NULL,
  started_at REAL, ended_at REAL, result TEXT
);
CREATE TABLE IF NOT EXISTS journal (
  cycle_ts TEXT PRIMARY KEY, body TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS threads (               -- THE single work queue
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT,
  kind TEXT NOT NULL DEFAULT 'work', status TEXT NOT NULL DEFAULT 'open',
  created_cycle TEXT, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (             -- artifact-bound, not intent-bound
  id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_ts TEXT, artifact TEXT NOT NULL,
  target_hashes TEXT, reasoning TEXT, status TEXT NOT NULL DEFAULT 'pending',
  ref TEXT, filed_at REAL NOT NULL, resolved_at REAL, expires_at REAL, last_event_id INTEGER
);
CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_ts TEXT NOT NULL,
  situation TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_cycle ON tasks(cycle_ts);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
"""

# Legal approval status transitions. Anything else is refused (defends against
# replayed events and out-of-order transport callbacks).
TRANSITIONS = {
    "pending": {"approved", "denied", "expired", "invalidated"},
    "approved": {"executed", "invalidated"},
}
THREAD_PRIORITY = "CASE kind WHEN 'approval' THEN 0 WHEN 'recovery' THEN 1 " \
                  "WHEN 'prune' THEN 2 WHEN 'audit' THEN 3 ELSE 4 END"


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=15)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def _init(self) -> None:
        c = self.connect()
        c.executescript(SCHEMA)
        c.commit()
        c.close()

    # --- approvals ---------------------------------------------------------
    def approval_transition(self, con, approval_id: int, new_status: str, event_id: int | None = None) -> bool:
        row = con.execute("SELECT status, last_event_id FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return False
        cur, last = row
        if new_status not in TRANSITIONS.get(cur, set()):
            return False
        if event_id is not None and last is not None and event_id <= last:
            return False  # replayed transport event
        r = con.execute(
            "UPDATE approvals SET status=?, resolved_at=?, last_event_id=COALESCE(?, last_event_id) "
            "WHERE id=? AND status=?", (new_status, time.time(), event_id, approval_id, cur))
        con.commit()
        return r.rowcount > 0

    # --- threads (the work queue) -----------------------------------------
    def add_thread(self, con, title: str, body: str = "", kind: str = "work", created_cycle: str | None = None) -> None:
        con.execute("INSERT INTO threads (title, body, kind, status, created_cycle, updated_at) "
                    "VALUES (?,?,?,'open',?,?)", (title, body, kind, created_cycle, time.time()))
        con.commit()

    def open_threads(self, con, limit: int = 50):
        return con.execute(f"SELECT id, kind, title FROM threads WHERE status='open' "
                           f"ORDER BY {THREAD_PRIORITY}, id LIMIT ?", (limit,)).fetchall()

    def has_open(self, con, kind: str) -> bool:
        return con.execute("SELECT 1 FROM threads WHERE kind=? AND status='open' LIMIT 1", (kind,)).fetchone() is not None

    def orphaned_cycle(self, con):
        return con.execute("SELECT ts FROM cycles WHERE status='running' ORDER BY started_at DESC LIMIT 1").fetchone()
