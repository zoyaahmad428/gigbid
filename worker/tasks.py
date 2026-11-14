"""The worker's jobs. Plain async functions of an Arq context, so tests call them directly.

  run_thread    run one operation on a graph thread (start, resume with a decision, retry)
  scout_all     every `worker.scout_every_minutes`: queue scout_user for users with auto-scout on
  scout_user    find new jobs in the user's category and start bid runs (each stops at approval)
  monitor_all   every `worker.monitor_every_minutes`: queue monitor_user for every user
  monitor_user  read marketplace events: draft replies to client messages, learn from outcomes
  recover       at startup: re-queue runs a dead worker left behind

Nothing here can send: bid and reply runs stop at the approval interrupt, and only the API can
resume one, with the human's decision.

Safe to repeat: Arq retries a job after a crash, and a scheduled tick may fire twice. Every run
goes through `Runtime.execute` (carries on from the last checkpoint, never starts over), each job
id is derived from what it does (the same request queued twice runs once), a Redis lock stops
two workers driving one thread at the same time, and the event cursor only moves after the
events were handled.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from agent.marketplace_port import JobQuery, MarketplaceError, make_adapter
from agent.memory import store as memory
from agent.runtime import bid_thread, convo_thread
from agent.settings import ROOT, config
from apps.api.runs import ACTIVE, agent_user, drive, job_id_for

log = logging.getLogger("gigbid.worker")
REPLY_EVENTS = {"client_message"}
OUTCOME_EVENTS = {"client_message", "proposal_declined", "proposal_closed", "hired"}


def wcfg() -> dict:
    return config().get("worker", {})


def category_of(persona: str) -> str:
    return json.loads((ROOT / f"data/personas/{persona}/profile.json").read_text())["category"]


async def market(ctx: dict, user: dict, method: str, *args, tries: int = 3):
    tok = user["settings"]["marketplace_token"]
    adapters = ctx.setdefault("adapters", {})
    if tok not in adapters:
        adapters[tok] = make_adapter(tok)
    for i in range(tries):
        try:
            return await getattr(adapters[tok], method)(*args)
        except MarketplaceError as e:
            if not e.retryable or i == tries - 1:
                raise
            await asyncio.sleep(1.0 * (i + 1))


def under_quota(store, user_id: str) -> bool:
    cap = int(config()["budgets"].get("max_runs_per_user_per_day", 0))
    return not cap or store.runs_started_since(user_id, time.time() - 86400) < cap


# ------------------------------------------------------------------ graph runs
async def run_thread(ctx: dict, thread_id: str, user_id: str, kind: str, op: str, args: dict) -> str:
    from arq import Retry

    store, rt = ctx["store"], ctx["rt"]
    u = store.user(user_id)
    if not u or not u["onboarded"]:
        return "no such user"
    lock = f"gigbid:lock:{thread_id}"
    if not await ctx["redis"].set(lock, ctx.get("worker_id", "w"), nx=True, ex=int(wcfg().get("job_timeout_seconds", 900))):
        raise Retry(defer=20)                                # another worker is driving this thread right now
    try:
        store.upsert_run(thread_id, user_id, kind, status="running", reason="picked up by the worker")
        return await drive(rt, store, thread_id, agent_user(u), kind, rt.execute(thread_id, agent_user(u), op, args))
    finally:
        await ctx["redis"].delete(lock)


async def queue_run(ctx: dict, thread_id: str, user_id: str, kind: str, op: str, args: dict, **meta) -> bool:
    """Record a run as queued and enqueue it, unless it is already queued or running."""
    store = ctx["store"]
    r = store.run(thread_id)
    if r and r["status"] in ACTIVE:
        return False
    args.setdefault("base", (await ctx["rt"].phase(thread_id))[1])
    store.upsert_run(thread_id, user_id, kind, status="queued", reason=meta.pop("reason", "queued"), **meta)
    store.add_event(thread_id, {"type": "started", "thread_id": thread_id, "op": op, "queued": True,
                                "by": "worker"})
    await ctx["redis"].enqueue_job("run_thread", thread_id, user_id, kind, op, args,
                                   _job_id=job_id_for(thread_id, op, args["base"]))
    return True


# ------------------------------------------------------------------ scheduled scouting
def tick(minutes: int) -> int:
    return int(time.time() // (60 * max(1, minutes)))


async def scout_all(ctx: dict) -> int:
    n = 0
    for u in ctx["store"].onboarded_users():
        if u["settings"].get("auto_scout"):
            await ctx["redis"].enqueue_job("scout_user", u["user_id"],
                                           _job_id=f"scout|{u['user_id']}|{tick(wcfg().get('scout_every_minutes', 30))}")
            n += 1
    return n


async def scout_user(ctx: dict, user_id: str) -> dict:
    store, c = ctx["store"], wcfg()
    u = store.user(user_id)
    if not u or not u["onboarded"] or not u["settings"].get("auto_scout"):
        return {"started": [], "why": "auto-scout is off"}
    category = category_of(u["settings"]["persona"])
    res = await market(ctx, u, "search_jobs", JobQuery(category=category, limit=int(c.get("scout_jobs_scanned", 20))))
    known = store.runs_by_job(user_id, [j["job_id"] for j in res["jobs"]])
    started = []
    for j in res["jobs"]:
        if len(started) >= int(c.get("scout_max_new_runs", 2)) or not under_quota(store, user_id):
            break
        if j["job_id"] in known:
            continue
        tid = bid_thread(user_id, j["job_id"])
        if await queue_run(ctx, tid, user_id, "bid", "start_bid", {"job_id": j["job_id"]}, job_id=j["job_id"],
                           title=j.get("title"), reason="found by the scout"):
            started.append(j["job_id"])
    log.info("scout %s: %d new run(s) in %s", user_id, len(started), category)
    return {"started": started}


# ------------------------------------------------------------------ marketplace events
async def monitor_all(ctx: dict) -> int:
    users = ctx["store"].onboarded_users()
    for u in users:
        await ctx["redis"].enqueue_job("monitor_user", u["user_id"],
                                       _job_id=f"monitor|{u['user_id']}|{tick(wcfg().get('monitor_every_minutes', 5))}")
    return len(users)


def p50_for(stats: dict, bid_type: str | None) -> float | None:
    key = "contract_hourly_rate_usd" if bid_type == "hourly" else "posted_fixed_budget_usd"
    return (stats.get(key) or {}).get("p50")


async def monitor_user(ctx: dict, user_id: str) -> dict:
    """New client messages start a reply draft; resolved proposals feed the outcome memory."""
    store = ctx["store"]
    u = store.user(user_id)
    if not u or not u["onboarded"]:
        return {}
    key = f"events:{user_id}:{u['settings']['marketplace_token']}"
    cursor = store.get_kv(key, 0)
    replies, outcomes, seq = [], [], cursor
    while True:
        page = await market(ctx, u, "get_events", seq)
        for ev in page["events"]:
            pid = ev.get("proposal_id")
            if ev["type"] in REPLY_EVENTS and pid and pid not in replies:
                replies.append(pid)
            if ev["type"] in OUTCOME_EVENTS and pid and pid not in outcomes:
                outcomes.append(pid)
        if not page["events"] or page["last_seq"] == seq:
            break
        seq = page["last_seq"]

    drafted, learned = [], {}
    if wcfg().get("auto_draft_replies", True):
        for pid in replies:
            if await draft_reply(ctx, u, pid):
                drafted.append(pid)
    stats: dict[str, dict] = {}                              # one lookup per category, not per proposal
    for pid in outcomes:
        if memory.get_store().get(("outcome_seen", user_id), pid):
            continue                                         # already counted: no marketplace calls needed
        p = await market(ctx, u, "get_proposal", pid)
        if p.get("status") not in memory.RESOLVED:
            continue
        cat = (await market(ctx, u, "get_job", p["job_id"])).get("category") or "unknown"
        if cat not in stats:
            stats[cat] = await market(ctx, u, "get_market_stats", cat)
        lessons = memory.record_outcome(user_id, p, cat, p50_for(stats[cat], p.get("bid_type")))
        if lessons is not None:
            learned[pid] = len(lessons)
    store.set_kv(key, seq)                                   # only after the events were handled
    return {"from": cursor, "to": seq, "drafted": drafted, "outcomes": learned}


async def draft_reply(ctx: dict, u: dict, proposal_id: str) -> bool:
    """The same checks as POST /api/proposals/{id}/reply: only the latest message, only if it is the client's."""
    store, uid = ctx["store"], u["user_id"]
    msgs = (await market(ctx, u, "list_messages", proposal_id))["messages"]
    if not msgs or msgs[-1].get("sender") == "freelancer" or not under_quota(store, uid):
        return False
    tid = convo_thread(uid, proposal_id, len(msgs))
    if store.run(tid):
        return False
    p = await market(ctx, u, "get_proposal", proposal_id)
    bid = store.run(bid_thread(uid, p["job_id"]))
    return await queue_run(ctx, tid, uid, "convo", "start_conversation",
                           {"proposal_id": proposal_id, "n_messages": len(msgs)}, job_id=p.get("job_id"),
                           proposal_id=proposal_id, title=(bid or {}).get("title") or p["job_id"],
                           reason="client message: drafting a reply")


# ------------------------------------------------------------------ startup recovery
async def recover(ctx: dict, *, older_than: float = 60) -> list[str]:
    """Runs marked queued/running with no job left in the queue (a worker was killed and Redis lost
    the job, or the API died after recording the run). Each is queued again; `Runtime.execute`
    carries on from its last checkpoint, or starts it if it never got one."""
    store = ctx["store"]
    queued = {j.args[0] for j in await ctx["redis"].queued_jobs() if j.function == "run_thread" and j.args}
    out = []
    rows = store._q("SELECT thread_id, user_id, kind, job_id, proposal_id, updated_at FROM runs "
                    "WHERE status IN ('queued','running') AND updated_at < ?", (time.time() - older_than,))
    for r in rows:
        tid = r["thread_id"]
        if tid in queued:
            continue
        if r["kind"] == "bid":
            op, args = "start_bid", {"job_id": r["job_id"]}
        else:
            op, args = "start_conversation", {"proposal_id": r["proposal_id"], "n_messages": int(tid.rsplit(":", 1)[1])}
        args["base"] = None                                  # never restarts a thread that already has checkpoints
        await ctx["redis"].enqueue_job("run_thread", tid, r["user_id"], r["kind"], op, args,
                                       _job_id=f"recover|{tid}|{int(r['updated_at'])}")
        out.append(tid)
    if out:
        log.warning("re-queued %d run(s) left behind by a stopped worker: %s", len(out), out)
    return out
