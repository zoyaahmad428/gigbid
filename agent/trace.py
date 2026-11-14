"""Execution trace store (kept apart from graph state and from long-term memory).

Every LLM call and tool call made while running a thread is appended here with
its node, inputs (PII-masked), a result summary, tokens, cost and latency.
Graph state holds only the thread id; the UI's run viewer and the R&D
evaluations read traces from here.

Langfuse (optional): with LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY set, the
same events also go to Langfuse (LANGFUSE_HOST, default cloud.langfuse.com)
through its public ingestion API: one trace per thread (session = thread id,
user = GigBid user id), a generation per LLM call with tokens and cost, a span
per tool call. The inputs sent are the PII-masked ones stored here. Sending
happens on a background thread in batches; if Langfuse is slow or down, events
are dropped and the run is not affected.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
        self.exporter: LangfuseExporter | None = LangfuseExporter.from_env()

    def add(self, kind: str, name: str, *, input: Any = None, output: Any = None, ok: bool = True, ms: float = 0,
            tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0, node: str | None = None) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO events (thread_id, ts, node, kind, name, input, output, ok, ms, tokens_in, tokens_out, "
                "cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_thread.get(), time.time(), node or _node.get(), kind, name, _dump(input), _dump(output), int(ok),
                 round(ms, 1), tokens_in, tokens_out, cost_usd))
        if self.exporter:
            self.exporter.emit(_thread.get(), node or _node.get(), kind, name, input, output, ok, ms, tokens_in,
                               tokens_out, cost_usd)

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


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(x: Any) -> Any:
    s = _dump(x)
    return None if s is None else json.loads(s) if not s.endswith("[truncated]") else s


class LangfuseExporter:
    """Batches trace events to Langfuse's ingestion API (POST /api/public/ingestion)."""

    def __init__(self, host: str, public_key: str, secret_key: str, *,
                 post: Callable[[list[dict]], None] | None = None, batch: int = 50, interval: float = 2.0):
        self.host, self.auth = host.rstrip("/"), (public_key, secret_key)
        self.q: queue.Queue = queue.Queue(maxsize=5000)
        self.batch, self.interval = batch, interval
        self._post = post or self._http_post
        self._traces: set[str] = set()
        self.sent = self.dropped = 0
        threading.Thread(target=self._loop, name="langfuse-export", daemon=True).start()

    @classmethod
    def from_env(cls) -> "LangfuseExporter | None":
        pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
        if not (pk and sk) or os.environ.get("GIGBID_LANGFUSE", "on") == "off":
            return None
        return cls(os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"), pk, sk)

    @staticmethod
    def trace_id(thread_id: str) -> str:
        return hashlib.sha256(thread_id.encode()).hexdigest()[:32]

    def events_for(self, thread_id: str | None, node: str | None, kind: str, name: str, input: Any, output: Any,
                   ok: bool, ms: float, tokens_in: int, tokens_out: int, cost_usd: float) -> list[dict]:
        thread_id = thread_id or "untracked"
        tid, now = self.trace_id(thread_id), time.time()
        out = []
        if tid not in self._traces:
            self._traces.add(tid)
            parts = thread_id.split(":")
            out.append({"type": "trace-create", "body": {
                "id": tid, "name": f"{parts[0]} thread", "sessionId": thread_id,
                "userId": parts[1] if len(parts) > 2 else None, "tags": ["gigbid", parts[0]],
                "metadata": {"thread_id": thread_id}}})
        body = {"id": uuid.uuid4().hex, "traceId": tid, "name": f"{node}: {name}" if node else name,
                "startTime": _iso(now - ms / 1000), "endTime": _iso(now), "input": _clip(input),
                "output": _clip(output), "level": "DEFAULT" if ok else "ERROR",
                "metadata": {"node": node, "kind": kind}}
        if kind == "llm":
            body.update(model=name, usage={"input": tokens_in, "output": tokens_out, "total": tokens_in + tokens_out,
                                           "unit": "TOKENS", "totalCost": cost_usd})
            out.append({"type": "generation-create", "body": body})
        else:
            out.append({"type": "span-create", "body": body})
        return [{"id": uuid.uuid4().hex, "timestamp": _iso(now), **e} for e in out]

    def emit(self, *args) -> None:
        try:
            for e in self.events_for(*args):
                self.q.put_nowait(e)
        except queue.Full:
            self.dropped += 1
        except Exception:                                    # tracing must never break a run
            logging.getLogger("gigbid.trace").debug("langfuse event skipped", exc_info=True)

    def flush(self) -> None:
        items = []
        while not self.q.empty():
            items.append(self.q.get_nowait())
        for i in range(0, len(items), self.batch):
            self._send(items[i:i + self.batch])

    def _send(self, items: list[dict]) -> None:
        if not items:
            return
        try:
            self._post(items)
            self.sent += len(items)
        except Exception as e:
            self.dropped += len(items)
            logging.getLogger("gigbid.trace").warning("Langfuse export failed (%d events dropped): %s", len(items), e)

    def _http_post(self, items: list[dict]) -> None:
        import httpx
        r = httpx.post(f"{self.host}/api/public/ingestion", json={"batch": items}, auth=self.auth, timeout=10)
        r.raise_for_status()

    def _loop(self) -> None:
        while True:
            items = [self.q.get()]
            deadline = time.time() + self.interval
            while len(items) < self.batch and time.time() < deadline:
                try:
                    items.append(self.q.get(timeout=max(0.01, deadline - time.time())))
                except queue.Empty:
                    break
            self._send(items)


TRACES = TraceStore(Path(os.environ.get("GIGBID_TRACE_DB", ROOT / "data/processed/traces.sqlite3")))


def bind_thread(thread_id: str):
    return _thread.set(thread_id)


def bind_node(node: str):
    return _node.set(node)


def current_thread() -> str | None:
    return _thread.get()
