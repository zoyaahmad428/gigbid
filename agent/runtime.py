"""Run, stream, resume and replay graph threads (used by the API, the worker, evals and demos).

`Runtime.execute(thread_id, user, op, args)` is the one entry point the API and the worker use:
it is safe to run twice, so a job the worker retries after a crash carries on from the last
checkpoint instead of starting the thread over.

Checkpointer: Postgres when DATABASE_URL is a Postgres URL (docker compose,
deployment), else SQLite in data/processed/ (local dev). Either way the state
of every thread survives a process restart, so a pending approval can be
resumed after the API is killed (assignment C6).

    async with Runtime() as rt:
        async for ev in rt.start_bid(user, "job_123"): ...      # streams node updates
        await rt.resume(thread_id, {"action": "approve"})
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path

from langgraph.types import Command

from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.handoffs import ApprovalDecision
from agent.memory.store import get_store
from agent.nodes.base import TERMINAL
from agent.settings import ROOT, postgres_url
from agent.trace import TRACES, bind_thread
from mcp_servers.common import User, bind


def bid_thread(user_id: str, job_id: str) -> str:
    return f"bid:{user_id}:{job_id}"


def convo_thread(user_id: str, proposal_id: str, n_messages: int) -> str:
    return f"convo:{user_id}:{proposal_id}:{n_messages}"


class Runtime:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or Path(os.environ.get("GIGBID_CHECKPOINT_DB", ROOT / "data/processed/checkpoints.sqlite3"))
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> "Runtime":
        url = postgres_url()
        if url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool
            # A pool, not one connection: the API serves several runs and requests at once, and a
            # pooled connection that Postgres dropped is replaced instead of failing every call.
            pool = AsyncConnectionPool(url, min_size=1, max_size=int(os.environ.get("GIGBID_PG_POOL", "5")),
                                       kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                                       check=AsyncConnectionPool.check_connection, open=False)
            await pool.open()
            self._stack.push_async_callback(pool.close)
            self.checkpointer = AsyncPostgresSaver(pool)
            await self.checkpointer.setup()
        else:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.checkpointer = await self._stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(self.db_path)))
        store = get_store()
        self.bid = build_bid_graph(self.checkpointer, store)
        self.convo = build_conversation_graph(self.checkpointer, store)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    def _graph(self, thread_id: str):
        return self.convo if thread_id.startswith("convo:") else self.bid

    @staticmethod
    def _bind(thread_id: str, user: dict) -> None:
        bind(User(user_id=user["user_id"], persona=user.get("persona"), marketplace_token=user.get("marketplace_token")),
             trace=thread_id.replace(":", "-"))
        bind_thread(thread_id)

    async def _stream(self, thread_id: str, user: dict, payload) -> AsyncIterator[dict]:
        self._bind(thread_id, user)
        cfg = {"configurable": {"thread_id": thread_id}}
        async for mode, chunk in self._graph(thread_id).astream(payload, cfg, stream_mode=["updates", "custom"]):
            if mode == "updates":
                for node_name, update in chunk.items():
                    if node_name == "__interrupt__":
                        yield {"type": "interrupt", "thread_id": thread_id, "payload": update[0].value}
                    else:
                        yield {"type": "node", "thread_id": thread_id, "node": node_name,
                               "status": (update or {}).get("status"), "reason": (update or {}).get("status_reason"),
                               "keys": sorted((update or {}).keys())}
            else:
                yield {"type": "custom", "thread_id": thread_id, "data": chunk}
        yield {"type": "done", "thread_id": thread_id, **(await self.summary(thread_id))}

    def start_bid(self, user: dict, job_id: str, *, job: dict | None = None, force_bid: bool = False):
        tid = bid_thread(user["user_id"], job_id)
        inp = {"thread_id": tid, "user": user, "job_id": job_id, "force_bid": force_bid, "steps": 0}
        if job:
            inp["job"] = job
        return self._stream(tid, user, inp)

    def start_conversation(self, user: dict, proposal_id: str, n_messages: int):
        tid = convo_thread(user["user_id"], proposal_id, n_messages)
        return self._stream(tid, user, {"thread_id": tid, "user": user, "proposal_id": proposal_id, "steps": 0})

    def resume(self, thread_id: str, user: dict, decision: dict):
        """Raises pydantic.ValidationError on a malformed decision, so a bad request from the UI
        is rejected up front and the pending approval stays pending (instead of dead-lettering)."""
        if thread_id.startswith("bid:"):
            ApprovalDecision.model_validate(decision)
        return self._stream(thread_id, user, Command(resume=decision))

    def continue_thread(self, thread_id: str, user: dict):
        """Carry on from the latest checkpoint (the process running it died between nodes)."""
        return self._stream(thread_id, user, None)

    async def phase(self, thread_id: str) -> tuple[str, str | None]:
        """Where a thread is: new | pending_work | awaiting_approval | finished, plus its checkpoint id."""
        st = await self.state(thread_id)
        if not st["values"]:
            return "new", None
        if st["pending_approval"]:
            return "awaiting_approval", st["checkpoint_id"]
        return ("pending_work" if st["next"] else "finished"), st["checkpoint_id"]

    async def retry_point(self, thread_id: str):
        g = self._graph(thread_id)
        async for snap in g.aget_state_history({"configurable": {"thread_id": thread_id}}):
            if snap.next and snap.values.get("status") not in TERMINAL:
                return snap.config
        return None

    async def execute(self, thread_id: str, user: dict, op: str, args: dict) -> AsyncIterator[dict]:
        """Run one operation on a thread so that running it twice is safe (the worker's jobs are
        retried after a crash, and the queue may deliver a job again).

        A thread a dead process left half-way carries on from its last checkpoint instead of
        starting again. Everything else happens only if the thread is still where it was when
        the operation was requested (`base` is the checkpoint id the caller saw); otherwise an
        earlier attempt already did it and we only report the result. Sending is still
        exactly-once on top of this through the idempotency keys in the submit node."""
        expected = {"start_bid": lambda: bid_thread(user["user_id"], args["job_id"]),
                    "start_conversation": lambda: convo_thread(user["user_id"], args["proposal_id"],
                                                               int(args["n_messages"]))}.get(op)
        if expected and expected() != thread_id:
            raise ValueError(f"{op} for {thread_id!r} would run thread {expected()!r}")
        phase, cid = await self.phase(thread_id)
        unchanged = cid == args.get("base")
        gen = None
        if phase == "pending_work":
            yield {"type": "resumed", "thread_id": thread_id, "from_checkpoint": cid}
            gen = self.continue_thread(thread_id, user)
        elif op == "start_bid" and (phase == "new" or (phase == "finished" and unchanged)):
            gen = self.start_bid(user, args["job_id"], force_bid=bool(args.get("force_bid")))
        elif op == "start_conversation" and phase == "new":
            gen = self.start_conversation(user, args["proposal_id"], int(args["n_messages"]))
        elif op == "resume" and phase == "awaiting_approval" and unchanged:
            gen = self.resume(thread_id, user, args["decision"])
        elif op == "retry" and phase == "finished" and unchanged:
            gen = self.retry(thread_id, user)
        if gen is None:
            yield {"type": "done", "thread_id": thread_id, "repeat": True, **(await self.summary(thread_id))}
            return
        async for ev in gen:
            yield ev

    async def retry(self, thread_id: str, user: dict):
        """Re-run a degraded / dead-lettered thread from the checkpoint before the node that failed.

        The failed node already wrote a terminal status and the graph routed to END, so resuming
        the latest checkpoint would do nothing. We fork from the last checkpoint that still had
        work queued (the original history stays for the audit trail)."""
        g = self._graph(thread_id)
        point = await self.retry_point(thread_id)
        if point is None:
            raise LookupError(f"{thread_id}: no checkpoint with pending work to retry from")
        cfg = await g.aupdate_state(point, {"status": "running", "status_reason": "retrying"})
        self._bind(thread_id, user)
        async for mode, chunk in g.astream(None, cfg, stream_mode=["updates"]):
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":
                    yield {"type": "interrupt", "thread_id": thread_id, "payload": update[0].value}
                else:
                    yield {"type": "node", "thread_id": thread_id, "node": node_name,
                           "status": (update or {}).get("status"), "reason": (update or {}).get("status_reason"),
                           "keys": sorted((update or {}).keys())}
        yield {"type": "done", "thread_id": thread_id, **(await self.summary(thread_id))}

    async def state(self, thread_id: str) -> dict:
        snap = await self._graph(thread_id).aget_state({"configurable": {"thread_id": thread_id}})
        pending = [i.value for t in snap.tasks for i in (t.interrupts or [])]
        return {"values": snap.values, "next": list(snap.next), "pending_approval": pending[0] if pending else None,
                "checkpoint_id": snap.config["configurable"].get("checkpoint_id")}

    async def summary(self, thread_id: str) -> dict:
        st = await self.state(thread_id)
        v = st["values"]
        status = "awaiting_approval" if st["pending_approval"] else v.get("status", "unknown")
        return {"status": status, "reason": v.get("status_reason"), "steps": v.get("steps", 0),
                "trace": TRACES.totals(thread_id)}

    async def history(self, thread_id: str) -> list[dict]:
        out = []
        async for snap in self._graph(thread_id).aget_state_history({"configurable": {"thread_id": thread_id}}):
            out.append({"checkpoint_id": snap.config["configurable"]["checkpoint_id"], "next": list(snap.next),
                        "status": snap.values.get("status"), "step": snap.metadata.get("step"),
                        "created_at": snap.created_at})
        return out

    def replay(self, thread_id: str, user: dict, checkpoint_id: str):
        """Re-run a thread from any earlier checkpoint (it forks; the original history stays)."""
        self._bind(thread_id, user)
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
        return self._graph(thread_id).astream(None, cfg, stream_mode="updates")
