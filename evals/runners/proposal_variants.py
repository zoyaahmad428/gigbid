"""R&D question B: does better retrieval give better proposals? V0 vs the shipped variant.

    python -m evals.runners.proposal_variants --variant v0_naive --profile open_qwen --per-persona 4
    python -m evals.runners.proposal_variants --variant combined --profile open_qwen --per-persona 4

The writer model comes from --profile (default `open`: gpt-oss-120b; `open_qwen`: qwen3.8-27b, used
for the reported run because of the free tier's daily token limits). It is the same for both
variants, so the comparison isolates retrieval.

For each job in the `proposals` set (15 "bid" dev jobs, 5 per persona) it starts from the state
the `decisions` run saved just before the writer (the same job spec, risk report and price for
both variants) and runs only what depends on retrieval: Fit Matcher -> Proposal Writer ->
claim verification. So the variants differ in retrieval and nothing else, and no analysis
tokens are spent twice. Jobs the shipped decision skipped are drafted anyway (as with "bid anyway"
in the UI), with a price from the Pricing node.

Writes evals/results/runs/prop_<variant>.jsonl: the first draft, the verified draft, claim
labels before and after verification, evidence ids, tokens and latency.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from evals.runners.common import RESULTS, WORK, ResultLog, eval_gigsim, job_set, tokens_by_model, user_for

APPEND = {"drafts", "critiques", "errors", "approvals"}


def merge(state: dict, upd: dict) -> dict:
    for k, v in (upd or {}).items():
        if k in APPEND:
            state[k] = list(state.get(k) or []) + list(v)
        elif k == "steps":
            state[k] = state.get(k, 0) + v
        elif k != "usage":
            state[k] = v
    return state


async def run(a) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from agent.llm import rate_waited
    from agent.graphs.bid_graph import build_bid_graph
    from agent.nodes.fit_matcher import fit_matcher
    from agent.nodes.pricing import pricing
    from agent.nodes.verifier import verifier
    from agent.nodes.writer import writer
    from agent.runtime import bid_thread
    from agent.trace import bind_thread
    from mcp_servers.common import User, bind

    tag = f"prop_{a.variant}"
    log = ResultLog(RESULTS / "runs" / f"{tag}.jsonl")
    async with AsyncSqliteSaver.from_conn_string(str(WORK / "ckpt_decisions.sqlite3")) as base_ckpt:
        base = build_bid_graph(base_ckpt)
        jobs, per = [], {}
        for j in job_set("proposals"):
            per[j["persona"]] = per.get(j["persona"], 0) + 1
            if per[j["persona"]] <= a.per_persona:
                jobs.append(j)
        for i, job in enumerate(jobs):
            if log.done(job["job_id"]):
                continue
            user = user_for(job["persona"])
            src = bid_thread(user["user_id"], job["job_id"])
            snap = await base.aget_state({"configurable": {"thread_id": src}})
            if not snap.values.get("job_spec"):
                print(f"[{i + 1}] {job['job_id']}: no saved analysis yet (run the decisions set first)", flush=True)
                continue
            st = {k: v for k, v in snap.values.items() if k not in ("usage", "status", "status_reason")}
            st.update(steps=0, status="running", force_bid=True)
            tid = f"{tag}:{job['job_id']}"
            bind(User(user_id=user["user_id"], persona=user["persona"], marketplace_token=user["marketplace_token"]),
                 trace=tid.replace(":", "-"))
            bind_thread(tid)
            t0 = time.perf_counter()
            stages = {}
            for name, fn in (("fit_matcher", fit_matcher), ("pricing", pricing), ("writer", writer),
                             ("verifier", verifier)):
                if name == "pricing" and st.get("price_quote"):
                    continue
                s0 = time.perf_counter()
                merge(st, await fn(st))
                stages[name] = round(time.perf_counter() - s0, 2)
                if st.get("status") not in (None, "running", "drafted", "verified", "priced"):
                    break
            written = [d for d in st.get("drafts", []) if d.get("stage") == "written"]
            ver = st.get("verification") or {}
            row = {"key": job["job_id"], "job_id": job["job_id"], "persona": job["persona"], "variant": a.variant,
                   "profile": a.profile,
                   "status": st.get("status"), "reason": st.get("status_reason"),
                   "fit": (st.get("fit_report") or {}).get("score"),
                   "fit_refused": (st.get("fit_report") or {}).get("refused"),
                   "evidence_ids": sorted((st.get("evidence") or {}).keys()),
                   "price": (st.get("price_quote") or {}).get("amount"),
                   "bid_type": (st.get("price_quote") or {}).get("bid_type"),
                   "first_draft": written[0]["cover_letter"] if written else None,
                   "draft": (st.get("draft") or {}).get("cover_letter"),
                   "answers": (st.get("draft") or {}).get("screening_answers"),
                   "verification_first": ver.get("stats_first_draft"), "verification": ver.get("stats"),
                   "claims": [{"sentence": r["sentence"], "label": r["label"], "score": r.get("score")}
                              for r in ver.get("results", [])],
                   "seconds": round(time.perf_counter() - t0, 2), "stage_seconds": stages,
                   "seconds_net": round(time.perf_counter() - t0 - rate_waited(t0, time.perf_counter()), 2),
                   "tokens": tokens_by_model(WORK / f"traces_{tag}.sqlite3", tid), "errors": st.get("errors"),
                   "error": None}
            log.add(row)
            print(f"[{i + 1}/{len(jobs)}] {job['job_id']} {a.variant}: {row['status']} fit={row['fit']} "
                  f"claims={row['verification_first']} {row['seconds']:.0f}s", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--profile", default="open")
    ap.add_argument("--per-persona", type=int, default=5)
    a = ap.parse_args()
    tag = f"prop_{a.variant}"
    os.environ.update({
        "GIGBID_DB": "sqlite", "MODEL_PROFILE": a.profile, "GIGBID_NO_FALLBACK": "1", "PORTFOLIO_RETRIEVER": a.variant,
        "GIGBID_RATE_WAIT": os.environ.get("GIGBID_RATE_WAIT", str(8 * 3600)),
        "GIGBID_TRACE_DB": str(WORK / f"traces_{tag}.sqlite3"), "GIGBID_STORE_DB": str(WORK / f"memory_{tag}.sqlite3"),
        "GIGBID_LANGFUSE": "off"})
    with eval_gigsim(tag) as url:
        os.environ["GIGSIM_BASE_URL"] = url
        asyncio.run(run(a))


if __name__ == "__main__":
    main()
