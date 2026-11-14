"""Run the shipped bid graph on a fixed job set and record everything the evaluations need.

    python -m evals.runners.graph_runs --set decisions --stop writer --tag decisions
    python -m evals.runners.graph_runs --set proposals --force-bid --stop critic --tag prop_combined
    python -m evals.runners.graph_runs --set proposals --force-bid --stop critic --retriever v0_naive --tag prop_v0
    python -m evals.runners.graph_runs --set orchestration --profile open_qwen --tag c_graph

Each tag gets its own checkpoints, traces, memory store and GigSim (data/processed/evals/), and
writes evals/results/runs/<tag>.jsonl, one row per job. Reruns skip finished jobs, and rate limits
pause the run instead of failing it (GIGBID_RATE_WAIT), so it can run across the free tier's
daily token limit. The cheap nodes' fallback model is switched off, so every call uses the model
under test.

--stop writer   stop after pricing (decision metrics; no 120b tokens)
--stop critic   stop after the first draft is verified (draft quality before the Critic loop)
--stop none     run to the approval interrupt (the full shipped pipeline)

--fork-from decisions   start each job from the state the `decisions` run saved before the writer
                        (same analysis, decision and price; no analysis tokens spent again). Jobs
                        that run skipped or watched keep that outcome. Tokens then include the
                        analysis tokens of the source run.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from evals.runners.common import (RESULTS, WORK, ResultLog, eval_gigsim, gold_by_id, job_set, net_seconds,
                                  tokens_by_model, user_for)


def configure(tag: str, profile: str, retriever: str | None) -> None:
    os.environ.update({
        "GIGBID_DB": "sqlite", "MODEL_PROFILE": profile, "GIGBID_NO_FALLBACK": "1",
        "GIGBID_RATE_WAIT": os.environ.get("GIGBID_RATE_WAIT", str(8 * 3600)),
        "GIGBID_CHECKPOINT_DB": str(WORK / f"ckpt_{tag}.sqlite3"), "GIGBID_TRACE_DB": str(WORK / f"traces_{tag}.sqlite3"),
        "GIGBID_STORE_DB": str(WORK / f"memory_{tag}.sqlite3"), "GIGBID_LANGFUSE": "off"})
    if retriever:
        os.environ["PORTFOLIO_RETRIEVER"] = retriever


def in_band(amount: float | None, band: dict | None) -> bool | None:
    if amount is None or not band or band.get("min") is None or band.get("max") is None:
        return None
    return band["min"] <= amount <= band["max"]


def row_for(job: dict, gold: dict, values: dict, nxt: list, seconds: float, tok: dict) -> dict:
    drafts = values.get("drafts") or []
    written = [d for d in drafts if d.get("stage") == "written"]
    ver = values.get("verification") or {}
    dec, risk, price = values.get("decision") or {}, values.get("risk_report") or {}, values.get("price_quote") or {}
    return {
        "key": job["job_id"], "job_id": job["job_id"], "persona": job["persona"], "kind": job["kind"],
        "gold_action": gold["gold_action"], "gold_skip_reason": gold["skip_reason"], "is_scam": gold["is_scam"],
        "has_injection": gold["has_injection"], "fair_price_band": gold.get("fair_price_band"),
        "status": values.get("status"), "reason": values.get("status_reason"), "next": nxt,
        "decision": dec.get("action"), "decision_reasons": dec.get("reasons"), "fit": dec.get("fit_score"),
        "risk": risk.get("score"), "risk_flags": risk.get("flags"), "injection_detected": risk.get("injection_detected"),
        "price": price.get("amount"), "bid_type": price.get("bid_type"),
        "price_in_band": in_band(price.get("amount"), gold.get("fair_price_band")),
        "first_draft": written[0]["cover_letter"] if written else None,
        "first_answers": written[0].get("screening_answers") if written else None,
        "draft": (values.get("draft") or {}).get("cover_letter"),
        "answers": (values.get("draft") or {}).get("screening_answers"),
        "verification_first": ver.get("stats_first_draft"), "verification": ver.get("stats"),
        "critic_rounds": len(values.get("critiques") or []),
        "critic_verdicts": [c.get("verdict") for c in values.get("critiques") or []],
        "compliance": values.get("compliance"),
        "evidence_ids": sorted((values.get("evidence") or {}).keys()),
        "seconds": round(seconds, 2), "tokens": tok, "errors": values.get("errors"),
    }


async def run(a) -> None:
    from agent.graphs.bid_graph import build_bid_graph
    from agent.llm import rate_waited
    from agent.memory.store import get_store
    from agent.runtime import Runtime, bid_thread
    from agent.trace import bind_thread
    from mcp_servers.common import User, bind

    stop = {"writer": ["writer"], "critic": ["critic"], "none": None}[a.stop]
    gold = gold_by_id()
    log = ResultLog(RESULTS / "runs" / f"{a.tag}.jsonl")
    jobs = job_set(a.set)[: a.limit]
    if a.only:
        jobs = [j for j in jobs if j["job_id"] in set(a.only.split(","))]
    src_graph = None
    if a.fork_from:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        src_cm = AsyncSqliteSaver.from_conn_string(str(WORK / f"ckpt_{a.fork_from}.sqlite3"))
        src_graph = build_bid_graph(await src_cm.__aenter__())
    async with Runtime() as rt:
        g = build_bid_graph(rt.checkpointer, get_store(), interrupt_before=stop)
        for jid in a.redo or []:                           # run a job again from scratch (e.g. after a fix)
            job = next(j for j in jobs if j["job_id"] == jid)
            await rt.checkpointer.adelete_thread(bid_thread(user_for(job["persona"])["user_id"], jid))
            log.rows = [r for r in log.rows if r["key"] != jid]
        for i, job in enumerate(jobs):
            if log.done(job["job_id"]):
                continue
            user = user_for(job["persona"])
            tid = bid_thread(user["user_id"], job["job_id"])
            bind(User(user_id=user["user_id"], persona=user["persona"], marketplace_token=user["marketplace_token"]),
                 trace=tid.replace(":", "-"))
            bind_thread(tid)
            cfg = {"configurable": {"thread_id": tid}}
            t0 = time.perf_counter()
            err = None
            try:
                snap = await g.aget_state(cfg)
                if not snap.values and src_graph is not None:
                    src = await src_graph.aget_state(cfg)
                    if not src.values:
                        raise LookupError(f"no {a.fork_from} state for {job['job_id']}")
                    vals = {k: v for k, v in src.values.items() if k != "usage"}
                    if list(src.next) == ["writer"]:          # the source stopped before the writer: carry on
                        await g.aupdate_state(cfg, vals, as_node="pricing")
                        async for _ in g.astream(None, cfg, stream_mode="updates"):
                            pass
                    else:                                    # skipped or watched there: same outcome here
                        await g.aupdate_state(cfg, vals, as_node="decision")
                elif not snap.values:
                    inp = {"thread_id": tid, "user": user, "job_id": job["job_id"], "force_bid": a.force_bid, "steps": 0}
                    async for _ in g.astream(inp, cfg, stream_mode="updates"):
                        pass
                elif snap.next and not any(t.interrupts for t in snap.tasks) and snap.next != tuple(stop or ()):
                    async for _ in g.astream(None, cfg, stream_mode="updates"):   # finish a run cut off earlier
                        pass
            except Exception as e:                                             # recorded, run continues
                err = f"{type(e).__name__}: {str(e)[:300]}"
            snap = await g.aget_state(cfg)
            tok = tokens_by_model(WORK / f"traces_{a.tag}.sqlite3", tid)
            if a.fork_from:
                for m, v in tokens_by_model(WORK / f"traces_{a.fork_from}.sqlite3", tid).items():
                    t = tok.setdefault(m, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0, "ms": 0.0})
                    for k in t:
                        t[k] += v[k]
            t1 = time.perf_counter()
            row = row_for(job, gold[job["job_id"]], snap.values, list(snap.next), t1 - t0, tok)
            row["rate_wait_s"] = rate_waited(t0, t1)
            row["seconds_net"] = round(row["seconds"] - row["rate_wait_s"], 2)
            if a.fork_from:
                src_row = next((r for r in ResultLog(RESULTS / "runs" / f"{a.fork_from}.jsonl").rows
                                if r["key"] == job["job_id"]), None)
                if src_row:
                    row["seconds"] = round(row["seconds"] + src_row["seconds"], 2)
                    src_net = net_seconds(src_row)
                    row["seconds_net"] = None if src_net is None else round(row["seconds_net"] + src_net, 2)
            row["error"] = err
            log.add(row)
            spent = sum(v["tokens_in"] + v["tokens_out"] for v in row["tokens"].values())
            print(f"[{i + 1}/{len(jobs)}] {job['job_id']} {job['kind']:<12} -> {row['status']} "
                  f"decision={row['decision']} {row['seconds']:.0f}s {spent} tok {err or ''}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=["decisions", "proposals", "orchestration", "injection", "smoke", "c"])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--profile", default="open")
    ap.add_argument("--retriever")
    ap.add_argument("--stop", default="none", choices=["writer", "critic", "none"])
    ap.add_argument("--force-bid", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="comma-separated job ids from the set (a targeted re-run)")
    ap.add_argument("--fork-from")
    ap.add_argument("--redo", nargs="*", help="job ids to run again from scratch")
    a = ap.parse_args()
    configure(a.tag, a.profile, a.retriever)
    with eval_gigsim(a.tag) as url:
        os.environ["GIGSIM_BASE_URL"] = url
        asyncio.run(run(a))


if __name__ == "__main__":
    main()
