"""Arq worker: long-running graph runs and the schedules.

    arq worker.main.WorkerSettings          (make worker; the `worker` service in docker compose)

Needs REDIS_URL. The API hands runs to it when started with GIGBID_RUNNER=worker; both share the
checkpointer (Postgres), the memory store (Postgres) and the app store (data/processed/app.sqlite3
on a shared volume), so the browser's live stream reads what the worker writes.
"""
from __future__ import annotations

import logging
import os
import socket

from arq import cron
from arq.connections import RedisSettings

from agent.runtime import Runtime
from agent.settings import config
from apps.api.store import AppStore
from worker import tasks

# Arq logs its own job lines; this adds GigBid's (without doubling Arq's through the root logger).
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
_log = logging.getLogger("gigbid")
_log.addHandler(_h)
_log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
_log.propagate = False
W = config().get("worker", {})


def every(minutes: int) -> set[int]:
    return set(range(0, 60, max(1, min(60, int(minutes)))))


async def startup(ctx: dict) -> None:
    # The MCP SDK configures root logging when the in-process servers are created; keep Arq's job
    # lines single and drop per-request HTTP logs (failed calls are in the trace store anyway).
    logging.getLogger("arq").propagate = False
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    ctx["worker_id"] = f"{socket.gethostname()}:{os.getpid()}"
    ctx["store"] = AppStore()
    ctx["rt_cm"] = Runtime()
    ctx["rt"] = await ctx["rt_cm"].__aenter__()
    await tasks.recover(ctx)


async def shutdown(ctx: dict) -> None:
    for a in ctx.get("adapters", {}).values():
        await a.aclose()
    if "rt_cm" in ctx:
        await ctx["rt_cm"].__aexit__(None, None, None)


class WorkerSettings:
    functions = [tasks.run_thread, tasks.scout_user, tasks.monitor_user]
    cron_jobs = [
        cron(tasks.scout_all, minute=every(W.get("scout_every_minutes", 30)), run_at_startup=False),
        cron(tasks.monitor_all, minute=every(W.get("monitor_every_minutes", 5)), run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = int(os.environ.get("GIGBID_WORKER_JOBS", "2"))    # Groq free tier: a couple of runs at a time
    job_timeout = int(W.get("job_timeout_seconds", 900))
    max_tries = int(W.get("max_tries", 3))
    keep_result = 3600
    allow_abort_jobs = True
