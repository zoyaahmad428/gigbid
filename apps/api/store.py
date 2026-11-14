"""The app's own data: users, sessions, runs and the events streamed from each run.

Kept apart from the graph checkpointer (agent/runtime.py), which owns thread state, and from the
trace store (agent/trace.py), which owns LLM/tool calls. This table of runs is an index for the UI:
which threads a user has, their latest status, and every progress event so a browser that
reconnects (or refreshes) replays the run from the start instead of losing it.

SQLite in WAL mode; the API and the worker (step 6) can share the file.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from agent.settings import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL, settings TEXT NOT NULL DEFAULT '{}', onboarded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
    job_id TEXT, proposal_id TEXT, title TEXT, status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL,
    updated_at REAL NOT NULL, summary TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS runs_user ON runs (user_id, updated_at);
CREATE TABLE IF NOT EXISTS run_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
    ts REAL NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS run_events_thread ON run_events (thread_id, seq);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
SESSION_DAYS = 14


def _hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"


def _check_pw(pw: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(_hash_pw(pw, bytes.fromhex(salt)), stored)


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AppStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.environ.get("GIGBID_APP_DB", ROOT / "data/processed/app.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ---- users and sessions
    def create_user(self, email: str, password: str) -> dict:
        uid = "u_" + secrets.token_hex(6)
        with self.lock:
            try:
                self.db.execute("INSERT INTO users (user_id, email, pw_hash, created_at) VALUES (?,?,?,?)",
                                (uid, email.lower(), _hash_pw(password), time.time()))
            except sqlite3.IntegrityError:
                raise ValueError("an account with this email already exists") from None
        return self.user(uid)

    def login(self, email: str, password: str) -> str | None:
        rows = self._q("SELECT user_id, pw_hash FROM users WHERE email=?", (email.lower(),))
        if not rows or not _check_pw(password, rows[0]["pw_hash"]):
            return None
        return self.new_session(rows[0]["user_id"])

    def new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._q("INSERT INTO sessions VALUES (?,?,?)", (_tok_hash(token), user_id, time.time() + SESSION_DAYS * 86400))
        return token

    def session_user(self, token: str) -> dict | None:
        rows = self._q("SELECT user_id, expires_at FROM sessions WHERE token_hash=?", (_tok_hash(token),))
        if not rows or rows[0]["expires_at"] < time.time():
            return None
        return self.user(rows[0]["user_id"])

    def logout(self, token: str) -> None:
        self._q("DELETE FROM sessions WHERE token_hash=?", (_tok_hash(token),))

    def user(self, user_id: str) -> dict | None:
        rows = self._q("SELECT user_id, email, created_at, settings, onboarded FROM users WHERE user_id=?", (user_id,))
        if not rows:
            return None
        r = dict(rows[0])
        r["settings"] = json.loads(r["settings"])
        r["onboarded"] = bool(r["onboarded"])
        return r

    def save_settings(self, user_id: str, settings: dict, onboarded: bool | None = None) -> dict:
        if onboarded is None:
            self._q("UPDATE users SET settings=? WHERE user_id=?", (json.dumps(settings), user_id))
        else:
            self._q("UPDATE users SET settings=?, onboarded=? WHERE user_id=?",
                    (json.dumps(settings), int(onboarded), user_id))
        return self.user(user_id)

    # ---- runs
    def upsert_run(self, thread_id: str, user_id: str, kind: str, *, status: str, reason: str | None = None,
                   job_id: str | None = None, proposal_id: str | None = None, title: str | None = None,
                   summary: dict | None = None) -> None:
        now = time.time()
        with self.lock:
            cur = self.db.execute("SELECT 1 FROM runs WHERE thread_id=?", (thread_id,)).fetchone()
            if cur:
                sets, args = ["status=?", "reason=?", "updated_at=?"], [status, reason, now]
                if title:
                    sets.append("title=?")
                    args.append(title)
                if summary is not None:
                    sets.append("summary=?")
                    args.append(json.dumps(summary))
                self.db.execute(f"UPDATE runs SET {', '.join(sets)} WHERE thread_id=?", (*args, thread_id))
            else:
                self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (thread_id, user_id, kind, job_id, proposal_id, title, status, reason, now, now,
                                 json.dumps(summary or {})))

    def run(self, thread_id: str) -> dict | None:
        rows = self._q("SELECT * FROM runs WHERE thread_id=?", (thread_id,))
        return _run_row(rows[0]) if rows else None

    def runs(self, user_id: str, *, kind: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM runs WHERE user_id=?", [user_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if status:
            sql += " AND status=?"
            args.append(status)
        return [_run_row(r) for r in self._q(sql + " ORDER BY updated_at DESC LIMIT ?", (*args, limit))]

    def runs_by_job(self, user_id: str, job_ids: list[str]) -> dict[str, dict]:
        if not job_ids:
            return {}
        marks = ",".join("?" * len(job_ids))
        rows = self._q(f"SELECT * FROM runs WHERE user_id=? AND kind='bid' AND job_id IN ({marks})",
                       (user_id, *job_ids))
        return {r["job_id"]: _run_row(r) for r in rows}

    def runs_started_since(self, user_id: str, since: float) -> int:
        return self._q("SELECT COUNT(*) FROM runs WHERE user_id=? AND created_at>=?", (user_id, since))[0][0]

    def onboarded_users(self) -> list[dict]:
        return [self.user(r["user_id"]) for r in self._q("SELECT user_id FROM users WHERE onboarded=1")]

    # ---- small worker state (event cursors)
    def get_kv(self, key: str, default: Any = None) -> Any:
        rows = self._q("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_kv(self, key: str, value: Any) -> None:
        self._q("INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))

    def mark_orphans(self) -> int:
        """Runs left 'running' by a process that died. Their checkpoints survive; the UI offers retry."""
        with self.lock:
            return self.db.execute("UPDATE runs SET status='interrupted', reason='the server stopped during this run; "
                                   "retry continues from the last checkpoint' WHERE status='running'").rowcount

    # ---- run events (what the SSE stream replays)
    def add_event(self, thread_id: str, data: dict) -> int:
        with self.lock:
            return self.db.execute("INSERT INTO run_events (thread_id, ts, data) VALUES (?,?,?)",
                                   (thread_id, time.time(), json.dumps(data, default=str))).lastrowid

    def events(self, thread_id: str, after: int = 0) -> list[dict]:
        return [{"seq": r["seq"], "ts": r["ts"], **json.loads(r["data"])}
                for r in self._q("SELECT * FROM run_events WHERE thread_id=? AND seq>? ORDER BY seq",
                                 (thread_id, after))]


def _run_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["summary"] = json.loads(d["summary"] or "{}")
    return d
