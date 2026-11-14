"""Worker tests: crash recovery from checkpoints, idempotent jobs, scouting and the event monitor.

No model, LLM key or GigSim is needed: a small stand-in graph replaces the bid graph, and the
marketplace is a fake. The checkpointer is SQLite, and also Postgres when GIGBID_TEST_POSTGRES_URL
is set (CI runs a Postgres service). The full Arq round trip runs when GIGBID_TEST_REDIS_URL is set.
"""
from __future__ import annotations

import operator
import os
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore

from agent.runtime import bid_thread

CALLS: dict[str, int] = {}
CRASH = {"b": 0}


class Toy(TypedDict, total=False):
    thread_id: str
    user: dict
    job_id: str
    force_bid: bool
    steps: int
    status: str
    log: Annotated[list, operator.add]


def node(name):
    async def run(state: Toy) -> dict:
        CALLS[name] = CALLS.get(name, 0) + 1
        if name == "b" and CRASH["b"] > 0:
            CRASH["b"] -= 1
            raise ConnectionAbortedError("worker killed")    # stands in for the process dying mid-node
        return {"log": [name], "status": "done" if name == "c" else "running"}
    return run


def toy_graph(checkpointer):
    g = StateGraph(Toy)
    for n in "abc":
        g.add_node(n, node(n))
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    return g.compile(checkpointer=checkpointer)


BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("GIGBID_TEST_POSTGRES_URL") else [])


@pytest.fixture(params=BACKENDS)
async def rt(request, tmp_path, monkeypatch):
    from agent.memory import store as memory
    from agent.runtime import Runtime
    memory.set_store(InMemoryStore())
    if request.param == "postgres":
        monkeypatch.setenv("DATABASE_URL", os.environ["GIGBID_TEST_POSTGRES_URL"])
        monkeypatch.setenv("GIGBID_DB", "auto")
    else:
        monkeypatch.setenv("GIGBID_DB", "sqlite")
    async with Runtime(db_path=tmp_path / "ckpt.sqlite3") as r:
        r.bid = toy_graph(r.checkpointer)
        CALLS.clear()
        CRASH["b"] = 0
        yield r


USER = {"user_id": "u_1", "persona": "ai_ml", "marketplace_token": "tok-ai"}


async def drain(gen) -> list[dict]:
    return [ev async for ev in gen]


async def test_a_crashed_run_carries_on_from_its_checkpoint_instead_of_starting_over(rt, tmp_path):
    job = f"job_{os.urandom(3).hex()}"
    tid = bid_thread("u_1", job)
    CRASH["b"] = 1
    with pytest.raises(ConnectionAbortedError):
        await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "base": None}))
    assert (await rt.phase(tid))[0] == "pending_work"
    # The queue delivers the same job again after the crash.
    evs = await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "base": None}))
    assert evs[0]["type"] == "resumed"
    assert CALLS == {"a": 1, "b": 2, "c": 1}                  # a was not run again
    assert evs[-1]["type"] == "done" and evs[-1]["status"] == "done"
    assert (await rt.state(tid))["values"]["log"] == ["a", "b", "c"]


async def test_a_job_delivered_twice_does_nothing_the_second_time(rt):
    job = f"job_{os.urandom(3).hex()}"
    tid = bid_thread("u_1", job)
    await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "base": None}))
    again = await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "base": None}))
    assert again == [{"type": "done", "thread_id": tid, "repeat": True, **(await rt.summary(tid))}]
    assert CALLS == {"a": 1, "b": 1, "c": 1}


async def test_bid_anyway_restarts_only_from_the_checkpoint_the_user_saw(rt):
    job = f"job_{os.urandom(3).hex()}"
    tid = bid_thread("u_1", job)
    await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "base": None}))
    seen = (await rt.phase(tid))[1]
    await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "force_bid": True, "base": seen}))
    assert CALLS["a"] == 2                                   # the user asked for a fresh run
    await drain(rt.execute(tid, USER, "start_bid", {"job_id": job, "force_bid": True, "base": seen}))
    assert CALLS["a"] == 2                                   # the same request again changes nothing


async def test_execute_refuses_a_thread_id_that_does_not_match_the_job(rt):
    with pytest.raises(ValueError):
        await drain(rt.execute("bid:u_1:other", USER, "start_bid", {"job_id": "job_x", "base": None}))


# ------------------------------------------------------------------ scouting and the event monitor
class FakeRedis:
    def __init__(self):
        self.jobs, self.keys = [], {}

    async def enqueue_job(self, fn, *args, _job_id=None, **kw):
        if _job_id and any(j[2] == _job_id for j in self.jobs):
            return None
        self.jobs.append((fn, args, _job_id))
        return object()

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.keys:
            return False
        self.keys[k] = v
        return True

    async def delete(self, k):
        self.keys.pop(k, None)

    async def queued_jobs(self):
        return []


class Market:
    def __init__(self):
        self.jobs = [{"job_id": f"job_{i}", "title": f"Job {i}", "category": "AI & Machine Learning"} for i in range(5)]
        self.events = []
        self.messages = {}
        self.proposals = {}

    async def search_jobs(self, q):
        return {"jobs": self.jobs[: q.limit], "total": len(self.jobs)}

    async def get_events(self, since):
        evs = [e for e in self.events if e["seq"] > since]
        return {"events": evs, "last_seq": evs[-1]["seq"] if evs else since}

    async def list_messages(self, pid):
        return {"messages": self.messages.get(pid, [])}

    async def get_proposal(self, pid):
        return self.proposals[pid]

    async def get_job(self, jid):
        return {"job_id": jid, "category": "AI & Machine Learning"}

    async def get_market_stats(self, cat):
        return {"posted_fixed_budget_usd": {"p50": 100.0}}

    async def aclose(self):
        pass


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    from agent.memory import store as memory
    from apps.api.store import AppStore
    memory.set_store(InMemoryStore())
    st = AppStore(tmp_path / "app.sqlite3")
    u = st.create_user("a@example.com", "password123")
    st.save_settings(u["user_id"], {"persona": "ai_ml", "name": "Sana", "hourly_rate": 55, "min_fixed_budget": 100,
                                    "marketplace_token": "tok-ai", "auto_scout": True}, onboarded=True)

    class RT:                                                 # phase() is all queue_run needs
        async def phase(self, tid):
            return "new", None

    m = Market()
    return {"store": st, "rt": RT(), "redis": FakeRedis(), "adapters": {"tok-ai": m}, "market": m,
            "uid": u["user_id"]}


async def test_scout_starts_a_few_runs_and_never_the_same_job_twice(ctx):
    from worker import tasks
    first = await tasks.scout_user(ctx, ctx["uid"])
    assert first["started"] == ["job_0", "job_1"]             # worker.scout_max_new_runs
    assert ctx["store"].run(f"bid:{ctx['uid']}:job_0")["status"] == "queued"
    second = await tasks.scout_user(ctx, ctx["uid"])
    assert second["started"] == ["job_2", "job_3"]
    run_jobs = [j for j in ctx["redis"].jobs if j[0] == "run_thread"]
    assert len(run_jobs) == 4 and len({j[1][0] for j in run_jobs}) == 4
    assert all("tok-ai" not in repr(j[1]) for j in run_jobs)  # no marketplace token on the queue


async def test_scout_does_nothing_when_auto_scout_is_off(ctx):
    from worker import tasks
    st = ctx["store"]
    s = st.user(ctx["uid"])["settings"]
    st.save_settings(ctx["uid"], {**s, "auto_scout": False})
    assert (await tasks.scout_user(ctx, ctx["uid"]))["started"] == []
    assert await tasks.scout_all(ctx) == 0


async def test_monitor_drafts_one_reply_per_client_message_and_learns_from_outcomes(ctx):
    from agent.memory import store as memory
    from worker import tasks
    m, uid = ctx["market"], ctx["uid"]
    m.proposals["p1"] = {"proposal_id": "p1", "job_id": "job_9", "status": "replied", "bid_type": "fixed",
                         "bid_amount": 90, "cover_letter": "Your RAG bot needs citations [ev_1]."}
    m.messages["p1"] = [{"sender": "freelancer", "text": "proposal"}, {"sender": "client", "text": "call?"}]
    m.events = [{"seq": 1, "type": "proposal_viewed", "proposal_id": "p1"},
                {"seq": 2, "type": "client_message", "proposal_id": "p1"}]
    out = await tasks.monitor_user(ctx, uid)
    assert out["drafted"] == ["p1"] and out["to"] == 2 and out["outcomes"] == {"p1": 0}
    assert ctx["store"].run(f"convo:{uid}:p1:2")["status"] == "queued"
    assert memory.get_store().get(("outcome_seen", uid), "p1") is not None
    again = await tasks.monitor_user(ctx, uid)                 # nothing new: no second draft, nothing counted twice
    assert again["drafted"] == [] and again["from"] == 2 and again["outcomes"] == {}


async def test_run_thread_waits_if_another_worker_holds_the_thread(ctx):
    from arq import Retry
    from worker import tasks
    tid = f"bid:{ctx['uid']}:job_0"
    ctx["redis"].keys[f"gigbid:lock:{tid}"] = "other-worker"
    with pytest.raises(Retry):
        await tasks.run_thread(ctx, tid, ctx["uid"], "bid", "start_bid", {"job_id": "job_0", "base": None})


# ------------------------------------------------------------------ the real queue (CI: Redis service)
@pytest.mark.skipif(not os.environ.get("GIGBID_TEST_REDIS_URL"), reason="needs GIGBID_TEST_REDIS_URL")
async def test_arq_round_trip_runs_a_queued_thread_to_the_end(rt, tmp_path):
    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.worker import Worker

    from apps.api.runs import RunManager
    from apps.api.store import AppStore
    from worker import tasks

    st = AppStore(tmp_path / "app.sqlite3")
    u = st.create_user("q@example.com", "password123")
    st.save_settings(u["user_id"], {"persona": "ai_ml", "name": "Q", "hourly_rate": 55, "min_fixed_budget": 100,
                                    "marketplace_token": "tok-ai"}, onboarded=True)
    redis = await create_pool(RedisSettings.from_dsn(os.environ["GIGBID_TEST_REDIS_URL"]))
    await redis.flushdb()
    try:
        rm = RunManager(rt, st, queue=redis)
        tid = f"bid:{u['user_id']}:job_q"
        user = {"user_id": u["user_id"]}
        await rm.launch(tid, user, "bid", "start_bid", {"job_id": "job_q"}, job_id="job_q")
        assert st.run(tid)["status"] == "queued" and rm.busy(tid)

        async def startup(ctx):
            ctx.update(store=st, rt=rt)

        w = Worker(functions=[tasks.run_thread], redis_pool=redis, burst=True, on_startup=startup,
                   poll_delay=0.05, max_jobs=2, handle_signals=False)
        await w.main()
        await w.close()
        assert st.run(tid)["status"] == "done"
        assert CALLS == {"a": 1, "b": 1, "c": 1}
        assert [e["type"] for e in st.events(tid)][0] == "started" and st.events(tid)[-1]["type"] == "done"
    finally:
        await redis.flushdb()
        await redis.aclose()
