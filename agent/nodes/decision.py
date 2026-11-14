"""Bid / watch / skip decision. Rules from config, no model: it must be explainable."""
from __future__ import annotations

from agent.handoffs import BidDecision
from agent.nodes.base import node
from agent.settings import config

HARD_FLAGS = {"scam_pattern", "credential_request", "off_platform_payment"}


def budget_too_low(job: dict, user: dict, pricing: dict) -> str | None:
    """The user's floor rule (a setting, like the hourly rate itself). None when the budget is fine
    or unknown: a job without a stated maximum is priced, not skipped."""
    b = job.get("budget") or {}
    mx = b.get("max")
    if not mx:
        return None
    rate = pricing.get("hourly_floor_usd") or user.get("hourly_rate")
    if b.get("type") == "hourly" and rate and mx < pricing["skip_if_hourly_max_below"] * rate:
        return f"client's maximum ${mx:g}/h is below {pricing['skip_if_hourly_max_below']:.0%} of your ${rate:g}/h rate"
    minimum = pricing.get("fixed_floor_usd") or user.get("min_fixed_budget")
    if b.get("type") == "fixed" and pricing.get("skip_if_fixed_max_below_minimum") and minimum and mx < minimum:
        return f"client's budget ${mx:g} is below your ${minimum:g} minimum for fixed-price work"
    return None


@node("decision")
async def decide(state: dict) -> dict:
    th, pricing = config()["thresholds"], config()["pricing"]
    risk, fit = state["risk_report"], state["fit_report"]
    reasons: list[str] = []
    hard = HARD_FLAGS & set(risk["flags"])
    low_budget = budget_too_low(state.get("job", {}), state.get("user", {}), pricing)
    if hard or risk["score"] > th["maximum_risk_score"]:
        action = "skip"
        reasons.append(f"risk {risk['score']:.2f}" + (f" with {', '.join(sorted(hard))}" if hard else "")
                       + f" (limit {th['maximum_risk_score']})")
    elif fit["refused"] or fit["score"] < th["watch_fit_score"]:
        action = "skip"
        reasons.append(f"fit {fit['score']:.2f} below {th['watch_fit_score']}"
                       + (": no supporting evidence" if fit["refused"] else ""))
    elif low_budget:
        action = "skip"
        reasons.append(low_budget)
    elif fit["score"] < th["minimum_fit_score"]:
        action = "watch"
        reasons.append(f"fit {fit['score']:.2f} is borderline (bid at {th['minimum_fit_score']})")
    else:
        action = "bid"
        reasons.append(f"fit {fit['score']:.2f} and risk {risk['score']:.2f} within limits")
    reasons += fit["reasons"][:2]
    if risk.get("injection_detected"):
        reasons.append("the post contains instructions aimed at AI tools; they were ignored")
    if state.get("force_bid") and action != "bid" and not hard:
        reasons.append("the user chose to bid anyway")
        action = "bid"
    auto = action == "skip" and bool(state.get("user", {}).get("autonomy_auto_skip",
                                                               config()["autonomy"]["auto_skip_low_fit"]))
    d = BidDecision(action=action, reasons=reasons, fit_score=fit["score"], risk_score=risk["score"], auto=auto)
    status = {"skip": "skipped", "watch": "watching", "bid": "running"}[action]
    return {"decision": d.model_dump(), "status": status, "status_reason": reasons[0]}
