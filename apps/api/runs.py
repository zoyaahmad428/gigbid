"""Runs graph threads in the background and records what they stream.

A request that starts or resumes a thread returns at once with the thread id; the graph runs as an
asyncio task here, and every event it yields is written to the app store. The browser reads
progress from GET /api/runs/{id}/stream, which replays the stored events and then follows new ones,
so a refresh or a second tab sees the same run. The pending approval itself lives in the
checkpointer, so it survives a restart of this process (assignment C6, D).

Step 6 moves execution into the Arq worker; the worker writes to the same store, so the stream
endpoint does not change.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from agent.runtime import Runtime
from apps.api.store import AppStore

log = logging.getLogger("gigbid.runs")
FINAL = {"skipped", "watching", "rejected", "blocked", "needs_attention", "dead_letter", "degraded", "submitted",
         "done", "replied", "awaiting_approval", "failed", "interrupted"}


class RunManager:
    def __init__(self, runtime: Runtime, store: AppStore, max_parallel: int = 2):
        self.rt, self.store = runtime, store
        self.sem = asyncio.Semaphore(max_parallel)          # Groq free tier: a few runs at a time
        self.tasks: dict[str, asyncio.Task] = {}

    def busy(self, thread_id: str) -> bool:
        t = self.tasks.get(thread_id)
        return bool(t and not t.done())

    def launch(self, thread_id: str, user: dict, kind: str, events: AsyncIterator[dict], **meta) -> None:
        if self.busy(thread_id):
            raise RuntimeError("this run is already in progress")
        self.store.upsert_run(thread_id, user["user_id"], kind, status="running", reason="queued", **meta)
        self.store.add_event(thread_id, {"type": "started", "thread_id": thread_id})
        self.tasks[thread_id] = asyncio.create_task(self._drive(thread_id, user, kind, events))

    async def _drive(self, thread_id: str, user: dict, kind: str, events: AsyncIterator[dict]) -> None:
        async with self.sem:
            try:
                async for ev in events:
                    self.store.add_event(thread_id, ev)
                    if ev["type"] == "node":
                        self.store.upsert_run(thread_id, user["user_id"], kind, status="running",
                                              reason=f"{ev['node']}: {ev['reason']}" if ev.get("reason") else ev["node"])
                    elif ev["type"] == "done":
                        self.store.upsert_run(thread_id, user["user_id"], kind, status=ev["status"],
                                              reason=ev.get("reason"), summary=await self.summary(thread_id))
            except Exception as e:                           # the graph handles its own failures; this is a bug
                log.exception("run %s crashed", thread_id)
                self.store.add_event(thread_id, {"type": "error", "thread_id": thread_id, "message": str(e)[:500]})
                self.store.upsert_run(thread_id, user["user_id"], kind, status="failed", reason=str(e)[:300])

    async def summary(self, thread_id: str) -> dict:
        """The few fields the inbox, pipeline and analytics need without loading the full state."""
        st = await self.rt.state(thread_id)
        v = st["values"]
        s = await self.rt.summary(thread_id)
        out = {"trace": s["trace"], "steps": s["steps"]}
        if thread_id.startswith("bid:"):
            out.update(decision=(v.get("decision") or {}).get("action"),
                       fit=(v.get("fit_report") or {}).get("score"), risk=(v.get("risk_report") or {}).get("score"),
                       bid=(v.get("price_quote") or {}).get("amount"),
                       bid_type=(v.get("price_quote") or {}).get("bid_type"),
                       proposal_id=((v.get("submission") or {}).get("result") or v.get("submission") or {}).get("proposal_id"),
                       connects=(v.get("submission") or {}).get("connects_cost"),
                       revisions=len(v.get("critiques") or []), edits=sum(a.get("action") == "edit"
                                                                         for a in v.get("approvals") or []))
        else:
            out.update(intent=(v.get("intent") or {}).get("intent"))
        return out
