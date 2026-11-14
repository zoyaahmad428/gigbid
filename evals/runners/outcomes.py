"""Simulated outcomes: submit drafts to a private GigSim and read replies and hires after two weeks.

    python -m evals.runners.outcomes --runs prop_v0_naive prop_combined c_graph c_react c_supervisor
    python -m evals.runners.outcomes --x2

For each runs file, every row with a proposal is submitted (draft + submit, the harness acting as the
approving human: this only ever happens in an evaluation GigSim) to a fresh GigSim of its own,
the simulated clock is moved forward 14 days, and each proposal's outcome is read: replied (any
non-scam client message: GigSim's own definition), interview, hired, or rejected at submission
(GigSim's rule checks, e.g. a missing screening answer). The Connects balance is topped up through
GigSim's admin endpoint so the weekly allowance doesn't cut a run short.

--x2  the signature extension (docs/EXTENSION.md): the same letters submitted twice, at the shipped
      price and at a 25% premium, to two GigSims; paired McNemar exact test on reply.

Writes evals/results/runs/outcomes_<name>.jsonl and evals/results/outcomes.json / x2_pricing.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

from evals.runners.common import RESULTS, ResultLog, TOKENS, admin, eval_gigsim

ACCOUNT = {"ai_ml": "acct_ai_ml", "fullstack": "acct_fullstack", "data_analyst": "acct_data_analyst"}
SIM_DAYS = 14


def rows_of(name: str) -> list[dict]:
    p = RESULTS / "runs" / f"{name}.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def proposals_from(name: str) -> list[dict]:
    out = []
    for r in rows_of(name):
        if not r.get("draft") or not r.get("price") or r.get("decision") in ("skip", "watch"):
            continue
        out.append({"job_id": r["job_id"], "persona": r["persona"], "letter": r["draft"],
                    "answers": r.get("answers") or [], "price": float(r["price"]),
                    "bid_type": r.get("bid_type") or "fixed"})
    return out


async def submit_all(url: str, items: list[dict], tag: str) -> list[dict]:
    from agent.marketplace_port import DraftIn, MarketplaceError
    from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
    for acct in ACCOUNT.values():
        admin(url, "POST", f"/admin/accounts/{acct}/connects", {"balance": 5000})
    out = []
    adapters = {p: GigSimAdapter(token=t, base_url=url) for p, t in TOKENS.items()}
    for it in items:
        ad = adapters[it["persona"]]
        res = {**it, "proposal_id": None, "rejected": None}
        try:
            d = await ad.create_proposal_draft(DraftIn(job_id=it["job_id"], cover_letter=it["letter"],
                                                       bid_amount=it["price"], bid_type=it["bid_type"],
                                                       screening_answers=it["answers"]), f"{tag}-d-{it['job_id']}")
            res["proposal_id"] = d["proposal_id"]
            await ad.submit_proposal(d["proposal_id"], f"{tag}-s-{it['job_id']}")
        except MarketplaceError as e:
            res["rejected"] = str(e)[:300]
        out.append(res)
    admin(url, "POST", "/admin/clock", {"advance_hours": SIM_DAYS * 24})
    for p, ad in adapters.items():
        props = {x["proposal_id"]: x for x in (await ad.list_proposals(None))["proposals"]}
        for r in out:
            if r["persona"] == p and r["proposal_id"] in props:
                pr = props[r["proposal_id"]]
                msgs = (await ad.list_messages(r["proposal_id"]))["messages"]
                client = [m for m in msgs if m.get("sender") == "client"]
                r.update(status=pr["status"], replied=bool(client) and pr["status"] != "draft",
                         interview=pr["status"] in ("interview", "hired") or any(
                             "call" in (m.get("text") or "").lower() for m in client),
                         hired=pr["status"] == "hired")
        await ad.aclose()
    return out


def rates(rs: list[dict]) -> dict:
    sub = [r for r in rs if r.get("proposal_id") and not r.get("rejected")]
    n = len(sub)
    return {"proposals": len(rs), "submitted": n, "rejected_at_submission": len(rs) - n,
            "reply_rate": round(sum(r.get("replied", False) for r in sub) / n, 4) if n else None,
            "interview_rate": round(sum(r.get("interview", False) for r in sub) / n, 4) if n else None,
            "hire_rate": round(sum(r.get("hired", False) for r in sub) / n, 4) if n else None}


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for b, c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * p), 4)


async def run_sources(names: list[str]) -> None:
    summary = json.loads((RESULTS / "outcomes.json").read_text()) if (RESULTS / "outcomes.json").exists() else {}
    for name in names:
        items = proposals_from(name)
        if not items:
            print(f"{name}: no proposals")
            continue
        with eval_gigsim(f"out_{name}", fresh=True) as url:
            res = await submit_all(url, items, f"out-{name}")
        log = ResultLog(RESULTS / "runs" / f"outcomes_{name}.jsonl")
        for r in res:
            log.add({"key": r["job_id"], **{k: v for k, v in r.items() if k not in ("letter", "answers")}})
        summary[name] = {**rates(res), "sim_days": SIM_DAYS,
                         "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        print(name, summary[name], flush=True)
    (RESULTS / "outcomes.json").write_text(json.dumps(summary, indent=1) + "\n")


async def run_x2(sources: list[str], premium: float) -> None:
    items, seen = [], set()
    for name in sources:
        for it in proposals_from(name):
            key = (it["job_id"], it["letter"][:80])
            if key not in seen:
                seen.add(key)
                items.append({**it, "source": name})
    # one GigSim account can apply to a job once: keep one letter per job per arm
    by_job: dict[str, dict] = {}
    for it in items:
        by_job.setdefault(it["job_id"], it)
    pairs = list(by_job.values())
    arms = {}
    for arm, factor in (("A_shipped_price", 1.0), ("B_premium_price", 1.0 + premium)):
        arm_items = [{**it, "price": round(it["price"] * factor, 2)} for it in pairs]
        with eval_gigsim(f"x2_{arm}", fresh=True) as url:
            arms[arm] = {r["job_id"]: r for r in await submit_all(url, arm_items, f"x2-{arm}")}
    a, b = arms["A_shipped_price"], arms["B_premium_price"]
    both = [j for j in a if a[j].get("proposal_id") and b[j].get("proposal_id") and not a[j].get("rejected")
            and not b[j].get("rejected")]
    only_a = sum(a[j].get("replied", False) and not b[j].get("replied", False) for j in both)
    only_b = sum(b[j].get("replied", False) and not a[j].get("replied", False) for j in both)
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": sources,
           "premium": premium, "sim_days": SIM_DAYS, "pairs": len(both),
           "A_shipped_price": rates(list(a.values())), "B_premium_price": rates(list(b.values())),
           "discordant_A_only": only_a, "discordant_B_only": only_b,
           "mcnemar_exact_p": mcnemar_exact(only_a, only_b),
           "per_job": [{"job_id": j, "A": a[j].get("status"), "B": b[j].get("status"), "price_A": a[j]["price"],
                        "price_B": b[j]["price"]} for j in both]}
    (RESULTS / "x2_pricing.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "per_job"}, indent=1))


def main() -> None:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--x2", action="store_true")
    ap.add_argument("--x2-sources", nargs="*", default=["prop_combined", "prop_v0_naive", "c_graph"])
    ap.add_argument("--premium", type=float, default=0.25)
    a = ap.parse_args()
    os.environ.setdefault("GIGBID_DB", "sqlite")
    if a.runs:
        asyncio.run(run_sources(a.runs))
    if a.x2:
        asyncio.run(run_x2(a.x2_sources, a.premium))


if __name__ == "__main__":
    main()
