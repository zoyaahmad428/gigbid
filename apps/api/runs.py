"""Runs graph threads in the background and records what they stream.

A request that starts or resumes a thread returns at once with the thread id. The work is one
operation (`start_bid`, `start_conversation`, `resume`, `retry`) run through `Runtime.execute`,
which is safe to repeat. Where it runs depends on the deployment:

  in-process  an asyncio task in the API (local dev, tests; `GIGBID_RUNNER=inprocess`, the default)
  worker      an Arq job on Redis, run by `worker/` (docker compose, deployment; `GIGBID_RUNNER=worker`)

Either way every event is written to the app store, and the browser reads progress from
GET /api/runs/{id}/stream, which replays the stored events and then follows new ones. The pending
approval itself lives in the checkpointer, so it survives a restart (assignment C6, D).
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator

from agent.runtime import Runtime
from apps.api.store import AppStore

log = logging.getLogger("gigbid.runs")
FINAL = {"skipped", "watching", "rejected", "blocked", "needs_attention", "dead_letter", "degraded", "submitted",
         "done", "replied", "awaiting_approval", "failed", "interrupted"}
ACTIVE = {"queued", "running"}
OPS = {"start_bid", "start_conversation", "resume", "retry"}


async def drive(rt: Runtime, store: AppStore, thread_id: str, user: dict, kind: str, events: AsyncIterator[dict]) -> str:
    """Write a run's events to the store as they arrive and keep its status current. Returns the final status."""
    status = "failed"
    try:
        async for ev in events:
            store.add_event(thread_id, ev)
            if ev["type"] == "node":
                store.upsert_run(thread_id, user["user_id"], kind, status="running",
                                 reason=f"{ev['node']}: {ev['reason']}" if ev.get("reason") else ev["node"])
            elif ev["type"] == "resumed":
                store.upsert_run(thread_id, user["user_id"], kind, status="running",
                                 reason="carrying on from the last checkpoint")
            elif ev["type"] == "done":
                status = ev["status"]
                store.upsert_run(thread_id, user["user_id"], kind, status=status, reason=ev.get("reason"),
                                 summary=await run_summary(rt, thread_id))
    except Exception as e:                                   # the graph handles its own failures; this is a bug
        log.exception("run %s crashed", thread_id)
        store.add_event(thread_id, {"type": "error", "thread_id": thread_id, "message": str(e)[:500]})
        store.upsert_run(thread_id, user["user_id"], kind, status="failed", reason=str(e)[:300])
    return status


async def run_summary(rt: Runtime, thread_id: str) -> dict:
    """The few fields the inbox, pipeline and analytics need without loading the full state."""
    st = await rt.state(thread_id)
    v = st["values"]
    s = await rt.summary(thread_id)
    out = {"trace": s["trace"], "steps": s["steps"]}
    if thread_id.startswith("bid:"):
        sub = v.get("submission") or {}
        out.update(decision=(v.get("decision") or {}).get("action"),
                   fit=(v.get("fit_report") or {}).get("score"), risk=(v.get("risk_report") or {}).get("score"),
                   bid=(v.get("price_quote") or {}).get("amount"),
                   bid_type=(v.get("price_quote") or {}).get("bid_type"),
                   proposal_id=(sub.get("result") or sub).get("proposal_id"),
                   connects=sub.get("connects_cost"),
                   revisions=len(v.get("critiques") or []),
                   edits=sum(a.get("action") == "edit" for a in v.get("approvals") or []))
    else:
        out.update(intent=(v.get("intent") or {}).get("intent"))
    return out


def agent_user(user: dict) -> dict:
    """The UserContext the graphs receive (agent/state.py), built from the user's saved settings."""
    s = user["settings"]
    return {"user_id": user["user_id"], "persona": s["persona"], "marketplace_token": s["marketplace_token"],
            "name": s["name"], "headline": s.get("headline", ""), "hourly_rate": s["hourly_rate"],
            "min_fixed_budget": s["min_fixed_budget"], "autonomy_auto_skip": s.get("autonomy_auto_skip", True),
            "timezone": s.get("timezone", "Asia/Karachi")}


def job_id_for(thread_id: str, op: str, base: str | None) -> str:
    """Arq job id: the same request queued twice (a double click, a retried scout tick) runs once."""
    return f"{thread_id}|{op}|{base or 'new'}"


class RunManager:
    def __init__(self, runtime: Runtime, store: AppStore, max_parallel: int = 2, queue=None):
        self.rt, self.store, self.queue = runtime, store, queue
        self.sem = asyncio.Semaphore(max_parallel)          # Groq free tier: a few runs at a time
        self.tasks: dict[str, asyncio.Task] = {}

    @property
    def mode(self) -> str:
        return "worker" if self.queue is not None else "inprocess"

    def busy(self, thread_id: str) -> bool:
        if self.queue is not None:
            r = self.store.run(thread_id)
            return bool(r and r["status"] in ACTIVE)
        t = self.tasks.get(thread_id)
        return bool(t and not t.done())

    async def launch(self, thread_id: str, user: dict, kind: str, op: str, args: dict | None = None, **meta) -> None:
        if op not in OPS:
            raise ValueError(f"unknown operation {op!r}")
        if self.busy(thread_id):
            raise RuntimeError("this run is already in progress")
        args = dict(args or {})
        args.setdefault("base", (await self.rt.phase(thread_id))[1])
        if self.queue is not None:
            self.store.upsert_run(thread_id, user["user_id"], kind, status="queued", reason="waiting for the worker",
                                  **meta)
            self.store.add_event(thread_id, {"type": "started", "thread_id": thread_id, "op": op, "queued": True})
            # Only the user id goes on the queue (no marketplace token in Redis); the worker re-reads
            # the user's settings from the app store.
            await self.queue.enqueue_job("run_thread", thread_id, user["user_id"], kind, op, args,
                                         _job_id=job_id_for(thread_id, op, args["base"]))
            return
        self.store.upsert_run(thread_id, user["user_id"], kind, status="running", reason="queued", **meta)
        self.store.add_event(thread_id, {"type": "started", "thread_id": thread_id, "op": op})
        self.tasks[thread_id] = asyncio.create_task(self._run(thread_id, user, kind, op, args))

    async def _run(self, thread_id: str, user: dict, kind: str, op: str, args: dict) -> None:
        async with self.sem:
            await drive(self.rt, self.store, thread_id, user, kind, self.rt.execute(thread_id, user, op, args))

    async def summary(self, thread_id: str) -> dict:
        return await run_summary(self.rt, thread_id)


async def make_queue():
    """An Arq pool when the API hands runs to the worker (GIGBID_RUNNER=worker), else None."""
    if os.environ.get("GIGBID_RUNNER", "inprocess") != "worker":
        return None
    from arq import create_pool
    from arq.connections import RedisSettings
    return await create_pool(RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0")))
