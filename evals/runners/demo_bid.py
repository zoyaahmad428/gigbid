"""Run one job through the bid graph against a running GigSim and stop at (or pass) human approval.

    make gigsim                                   # in another terminal
    python -m evals.runners.demo_bid --persona ai_ml --job job_xxx          # stop at approval
    python -m evals.runners.demo_bid --persona ai_ml --job job_xxx --approve
    python -m evals.runners.demo_bid --resume bid:dev_ai_ml:job_xxx --approve  # after a restart

Without --job it picks the first "bid" job of the persona's dev set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKENS = {"ai_ml": "dev-token-ai-ml", "fullstack": "dev-token-fullstack", "data_analyst": "dev-token-data-analyst"}


def user_for(persona: str) -> dict:
    """The demo user's settings, as the app's onboarding would store them (from the persona profile)."""
    p = json.loads((ROOT / f"data/personas/{persona}/profile.json").read_text())
    return {"user_id": f"dev_{persona}", "persona": persona, "marketplace_token": TOKENS[persona], "name": p["name"],
            "headline": p["headline"], "hourly_rate": p["hourly_rate_usd"], "min_fixed_budget": p["min_fixed_budget_usd"],
            "autonomy_auto_skip": True, "timezone": "Asia/Karachi"}


def pick_job(persona: str) -> str:
    for line in open(ROOT / f"evals/datasets/dev_{persona}.jsonl"):
        x = json.loads(line)
        if x["gold_action"] == "bid" and not x["has_injection"]:
            return x["job"]["job_id"]
    raise LookupError


def show(ev: dict) -> None:
    if ev["type"] == "node":
        print(f"  [{ev['node']:>14}] " + (f"status={ev['status']} " if ev["status"] else "") + (ev["reason"] or ""))
    elif ev["type"] == "interrupt":
        p = ev["payload"]
        print("\n=== APPROVAL NEEDED ===")
        if p["kind"] == "proposal":
            print(f"Job: {p['job']['title']}")
            print(f"Decision: {p['decision']['action']} — {'; '.join(p['decision']['reasons'][:2])}")
            print(f"Risk {p['risk_report']['score']} flags={p['risk_report']['flags']}")
            print(f"Fit {p['fit_report']['score']}: " + "; ".join(f"{m['status']}: {m['requirement'][:60]}"
                                                                for m in p['fit_report']['matrix']))
            q = p["price_quote"]
            print(f"Bid ${q['amount']} {q['bid_type']} | " + " | ".join(q["rationale"]))
            print(f"Verification: {p['verification']}")
            print(f"Critic rounds: {len(p['critiques'])}; last verdict {p['critiques'][-1]['verdict'] if p['critiques'] else '-'}")
            print(f"Compliance: allowed={p['compliance']['allowed']} violations={p['compliance']['violations']}")
            pv = p["preview"]
            print(f"Dry run: {pv['connects_cost']} Connects, balance after {pv['connects_balance_after']}, checks {pv['checks']}")
            print("\n--- cover letter ---\n" + p["draft"]["cover_letter"])
            for q_, a in zip(p.get("draft", {}).get("screening_answers", []), range(9)):
                print(f"\n--- answer {a + 1} ---\n{q_}")
        else:
            print(json.dumps(p, indent=1)[:2000])
    elif ev["type"] == "done":
        print(f"\n=== {ev['status']} === {ev.get('reason') or ''}  steps={ev['steps']}  trace={ev['trace']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="ai_ml", choices=list(TOKENS))
    ap.add_argument("--job")
    ap.add_argument("--resume", help="thread id to resume")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reject", help="reject with this reason")
    ap.add_argument("--edit-file", help="approve with the cover letter replaced by this file's text")
    a = ap.parse_args()
    os.environ.setdefault("GIGSIM_TOKEN", TOKENS[a.persona])
    from agent.runtime import Runtime

    user = user_for(a.persona)
    async with Runtime() as rt:
        if a.resume:
            tid = a.resume
        else:
            job_id = a.job or pick_job(a.persona)
            tid = None
            async for ev in rt.start_bid(user, job_id):
                show(ev)
                tid = ev["thread_id"]
        st = await rt.state(tid)
        if not st["pending_approval"]:
            return
        decision = None
        if a.reject:
            decision = {"action": "reject", "reason": a.reject}
        elif a.edit_file:
            decision = {"action": "edit", "edited_text": Path(a.edit_file).read_text()}
        elif a.approve:
            decision = {"action": "approve"}
        if decision:
            print(f"\n>>> resuming {tid} with {decision['action']}")
            async for ev in rt.resume(tid, user, decision):
                show(ev)
        else:
            print(f"\nPending. Resume later with:  python -m evals.runners.demo_bid --resume {tid} --approve")


if __name__ == "__main__":
    asyncio.run(main())
