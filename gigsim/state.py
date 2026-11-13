"""Mutable marketplace state in SQLite: clock, accounts, proposals, messages,
events and the scheduled client actions. One file, so state survives restarts."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clock (id INTEGER PRIMARY KEY CHECK (id = 1),
    real_anchor REAL NOT NULL, sim_anchor TEXT NOT NULL, speed REAL NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (token TEXT PRIMARY KEY, account_id TEXT UNIQUE NOT NULL,
    persona TEXT NOT NULL, connects INTEGER NOT NULL, week TEXT NOT NULL, strikes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS proposals (proposal_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
    job_id TEXT NOT NULL, client_id TEXT NOT NULL, status TEXT NOT NULL, client_state TEXT,
    cover_letter TEXT NOT NULL, screening_answers TEXT NOT NULL, bid_amount REAL NOT NULL,
    bid_type TEXT NOT NULL, connects_spent INTEGER NOT NULL DEFAULT 0, features TEXT,
    created_at TEXT NOT NULL, submitted_at TEXT, updated_at TEXT NOT NULL,
    UNIQUE (account_id, job_id));
CREATE TABLE IF NOT EXISTS messages (message_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL,
    sender TEXT NOT NULL, intent TEXT, text TEXT NOT NULL, sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
    type TEXT NOT NULL, proposal_id TEXT, job_id TEXT, at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scheduled (id INTEGER PRIMARY KEY AUTOINCREMENT, due TEXT NOT NULL,
    proposal_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS idempotency (account_id TEXT NOT NULL, key TEXT NOT NULL, endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL, response TEXT NOT NULL, status_code INTEGER NOT NULL,
    PRIMARY KEY (account_id, key));
CREATE INDEX IF NOT EXISTS sched_due ON scheduled (done, due);
CREATE INDEX IF NOT EXISTS events_acct ON events (account_id, seq);
"""


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def week_of(t: dt.datetime) -> str:
    y, w, _ = t.isocalendar()
    return f"{y}-W{w:02d}"


class State:
    def __init__(self, path: Path, clock_start: str, speed: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        if not self.db.execute("SELECT 1 FROM clock").fetchone():
            self.db.execute("INSERT INTO clock VALUES (1, ?, ?, ?)", (time.time(), clock_start, speed))

    # ------------------------------------------------------------ clock
    def now(self) -> dt.datetime:
        c = self.db.execute("SELECT * FROM clock").fetchone()
        return parse(c["sim_anchor"]) + dt.timedelta(seconds=(time.time() - c["real_anchor"]) * c["speed"])

    def set_clock(self, at: dt.datetime | None = None, speed: float | None = None) -> None:
        with self.lock:
            cur = self.now()
            c = self.db.execute("SELECT speed FROM clock").fetchone()
            self.db.execute("UPDATE clock SET real_anchor=?, sim_anchor=?, speed=? WHERE id=1",
                            (time.time(), iso(at or cur), c["speed"] if speed is None else speed))

    # ------------------------------------------------------------ accounts
    def ensure_account(self, token: str, persona: str, allowance: int) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
            if not row:
                aid = f"acct_{persona}"
                self.db.execute("INSERT OR IGNORE INTO accounts VALUES (?,?,?,?,?,0)",
                                (token, aid, persona, allowance, week_of(self.now())))
                row = self.db.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
            return dict(row)

    def account(self, token: str, allowance: int, rollover: bool) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
            if not row:
                return None
            wk = week_of(self.now())
            if row["week"] != wk:                    # weekly refill
                bal = row["connects"] + allowance if rollover else allowance
                self.db.execute("UPDATE accounts SET connects=?, week=? WHERE token=?", (bal, wk, token))
                row = self.db.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
            return dict(row)

    # ------------------------------------------------------------ idempotency
    def idem_get(self, account_id: str, key: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM idempotency WHERE account_id=? AND key=?",
                               (account_id, key)).fetchone()

    def idem_put(self, account_id: str, key: str, endpoint: str, req_hash: str, response: dict,
                 code: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO idempotency VALUES (?,?,?,?,?,?)",
                        (account_id, key, endpoint, req_hash, json.dumps(response), code))

    # ------------------------------------------------------------ events
    def emit(self, account_id: str, etype: str, proposal_id: str | None, job_id: str | None,
             payload: dict, at: dt.datetime | None = None) -> None:
        self.db.execute("INSERT INTO events (account_id, type, proposal_id, job_id, at, payload) VALUES (?,?,?,?,?,?)",
                        (account_id, etype, proposal_id, job_id, iso(at or self.now()), json.dumps(payload)))

    def schedule(self, proposal_id: str, base: dt.datetime, plan: list[tuple[float, str, dict]]) -> None:
        for hours, etype, payload in plan:
            self.db.execute("INSERT INTO scheduled (due, proposal_id, type, payload) VALUES (?,?,?,?)",
                            (iso(base + dt.timedelta(hours=hours)), proposal_id, etype, json.dumps(payload)))

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
