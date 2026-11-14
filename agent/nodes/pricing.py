"""Pricing Strategist: bid from market statistics, the client's budget and spend, and the user's floor;
decide whether the Connects are worth spending. Rule-based (explainable, no model needed)."""
from __future__ import annotations

from agent.handoffs import PriceQuote
from agent.nodes.base import node, tools
from agent.settings import config


@node("pricing")
async def pricing(state: dict) -> dict:
    j, user, fit = state["job"], state.get("user", {}), state["fit_report"]
    pc, b = config()["pricing"], j["budget"]
    stats = await tools().call("pricing", "get_market_stats", {"category": j.get("category") or "Other"})
    bal = await tools().call("pricing", "get_connects_balance", {})
    client = await tools().call("pricing", "get_client_history", {"client_id": j["client_id"]})
    why: list[str] = []
    if b["type"] == "hourly":
        q = stats.get("contract_hourly_rate_usd", {})
        band = (q.get("p25"), q.get("p75")) if q.get("p25") else None
        floor = float(pc.get("hourly_floor_usd") or user.get("hourly_rate") or q.get("p50") or 30)
        target = q.get(pc["market_percentile_target"], floor)
        amount = max(floor, target)
        why.append(f"market contract rate for {j.get('category')}: p25 ${q.get('p25')}, p50 ${q.get('p50')}, "
                   f"p75 ${q.get('p75')} per hour")
        if b.get("max"):
            if b["max"] < floor:
                why.append(f"client's maximum ${b['max']:g}/h is below your floor ${floor:g}/h; bidding the floor")
            else:
                amount = min(amount, b["max"])
                why.append(f"capped at the client's posted maximum ${b['max']:g}/h")
    else:
        q = stats.get("posted_fixed_budget_usd", {})
        band = (q.get("p25"), q.get("p75")) if q.get("p25") else None
        floor = float(pc.get("fixed_floor_usd") or user.get("min_fixed_budget") or 100)
        if b.get("max"):
            amount = max(floor, round(b["max"] * 0.95, -1) or b["max"])
            why.append(f"client budget ${b['max']:g}; bidding 95% to stay inside it")
        else:
            amount = max(floor, q.get("p50") or floor)
            why.append(f"no budget posted; market median for {j.get('category')} is ${q.get('p50')}")
        if b.get("max") and b["max"] < floor:
            why.append(f"client budget is below your minimum ${floor:g}")
    spent = client.get("total_spent_usd") or 0
    if spent > 10_000 and client.get("hire_rate", 0) > 0.5:
        why.append(f"client has spent ${spent:,.0f} and hires often; no discount needed")
    cost = int(j["connects_cost"])
    balance = int(bal.get("connects_balance", 0))
    worth = balance - cost >= int(pc["connects_reserve"]) and fit["score"] >= config()["thresholds"]["minimum_fit_score"]
    why.append(f"{cost} Connects of {balance} available; "
               + ("worth it at this fit" if worth else "keep for better-fitting jobs or top up first"))
    quote = PriceQuote(bid_type=b["type"], amount=round(float(amount), 2), market_band=band, floor=floor,
                       connects_cost=cost, connects_balance=balance, worth_connects=worth, rationale=why)
    return {"price_quote": quote.model_dump()}
