"""Execution trace store (kept apart from graph state and from long-term memory).

Every LLM call and tool call made while running a thread is appended here with
its node, inputs (PII-masked), a result summary, tokens, cost and latency.
Graph state holds only the thread id; the UI's run viewer and the R&D
evaluations read traces from here. If Langfuse is configured (step 6) the same
events are also sent there.
"""
from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from agent.settings import ROOT
from mcp_servers.common import mask

_thread: contextvars.ContextVar[str | None] = contextvars.ContextVar("gigbid_thread", default=None)
_node: contextvars.ContextVar[str | None] = contextvars.ContextVar("gigbid_node", default=None)


class TraceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT,
            ts REAL, node TEXT, kind TEXT, name TEXT, input TEXT, output TEXT, ok INTEGER, ms REAL,
            tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ev_thread ON events (thread_id, seq)")
        self.lock = threading.Lock()

    def add(self, kind: str, name: str, *, input: Any = None, output: Any = None, ok: bool = True, ms: float = 0,
            tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0, node: str | None = None) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO events (thread_id, ts, node, kind, name, input, output, ok, ms, tokens_in, tokens_out, "
                "cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_thread.get(), time.time(), node or _node.get(), kind, name, _dump(input), _dump(output), int(ok),
                 round(ms, 1), tokens_in, tokens_out, cost_usd))

    def events(self, thread_id: str) -> list[dict]:
        cur = self.db.execute("SELECT * FROM events WHERE thread_id=? ORDER BY seq", (thread_id,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def totals(self, thread_id: str) -> dict:
        r = self.db.execute("SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), COALESCE(SUM(cost_usd),0),"
                            " COUNT(*) FROM events WHERE thread_id=?", (thread_id,)).fetchone()
        return {"tokens_in": r[0], "tokens_out": r[1], "cost_usd": round(r[2], 6), "events": r[3]}


def _dump(x: Any) -> str | None:
    if x is None:
        return None
    s = json.dumps(mask(x), default=str, ensure_ascii=False)
    return s if len(s) <= 8000 else s[:8000] + "...[truncated]"


TRACES = TraceStore(Path(os.environ.get("GIGBID_TRACE_DB", ROOT / "data/processed/traces.sqlite3")))


def bind_thread(thread_id: str):
    return _thread.set(thread_id)


def bind_node(node: str):
    return _node.set(node)


def current_thread() -> str | None:
    return _thread.get()
