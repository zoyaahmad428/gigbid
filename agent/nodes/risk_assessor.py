"""Risk Assessor: client quality, scams, free-work and off-platform asks, injected instructions.

Deterministic signals (client history, scam phrases, injection patterns, the
Prompt Guard classifier) are computed first and passed to the small model,
which writes the report. The final score can never be *lower* than the floor
the deterministic signals set: a model fooled by the post cannot clear a scam.
"""
from __future__ import annotations

import re

from agent.guards import detect_injection, wrap
from agent.handoffs import RiskReport
from agent.llm import injection_probability, structured
from agent.nodes.base import node, to_thread, tools, usage_entry
from agent.settings import config

SCAM_RULES = [
    ("free_work_request", r"\b(unpaid|free|no payment)\b[^.]{0,60}\b(test|trial|sample|task)\b|"
                          r"\b(test|trial|sample)\b[^.]{0,40}\b(unpaid|free|no payment)\b", 0.75),
    ("off_platform_payment", r"\b(outside|off)[- ]?(the )?platform\b|\b(crypto|bitcoin|usdt|western union|wire "
                             r"transfer|bank transfer)\b|avoid (platform )?fees", 0.85),
    ("off_platform_contact", r"\b(whatsapp|telegram|skype|wechat|signal)\b|\b(email|message|text) me (at|on)\b", 0.6),
    ("credential_request", r"\b(log ?in|sign ?in) (with|using) your\b|\b(photo|copy|scan) of your (id|passport)\b|"
                           r"\bbank details\b|\bverify you\b", 0.85),
    ("scam_pattern", r"\bcheck for \$|\bmail you a check\b|send the difference|\bearn (up to )?\$[\d,]+ ?(per|/) ?"
                     r"(week|day)\b|no experience (required|needed)", 0.8),
]


def signals(job: dict, client: dict) -> tuple[list[str], float, dict]:
    text = f"{job['title']}\n{job['description']}\n" + "\n".join(job.get("screening_questions") or [])
    flags, floor = [], 0.0
    for flag, rx, f in SCAM_RULES:
        if re.search(rx, text, re.I):
            flags.append(flag)
            floor = max(floor, f)
    if not client.get("payment_verified"):
        flags.append("unverified_payment")
    if client.get("hires", 0) == 0 and client.get("total_spent_usd", 0) == 0:
        flags.append("new_client_no_history")
    if client.get("rating") is not None and client["rating"] < 4.0:
        flags.append("low_client_rating")
    b = job["budget"]
    if b["type"] == "fixed" and b.get("max") is not None and b["max"] <= 5:
        flags.append("budget_unrealistic")
    if "unverified_payment" in flags and "new_client_no_history" in flags:
        floor = max(floor, 0.35)
    hit, excerpt = detect_injection(text)
    prob = injection_probability(text)
    inj = hit or (prob is not None and prob >= config()["thresholds"]["injection_classifier_threshold"])
    if inj:
        flags.append("injected_instructions")
    quality = 0.0
    if client:
        quality = (0.35 * bool(client.get("payment_verified")) + 0.25 * min(1.0, client.get("hires", 0) / 10)
                   + 0.2 * min(1.0, (client.get("total_spent_usd") or 0) / 10_000)
                   + 0.2 * ((client.get("rating") or 0) / 5 if client.get("rating") is not None else 0))
    return sorted(set(flags)), floor, {"injection_regex": excerpt, "injection_classifier": prob,
                                       "injection": inj, "client_quality": round(quality, 3)}


@node("risk_assessor")
async def risk_assessor(state: dict) -> dict:
    j = state["job"]
    client = await tools().call("risk_assessor", "get_client_history", {"client_id": j["client_id"]})
    flags, floor, info = await to_thread(signals, j, client)
    public = {k: client.get(k) for k in ("payment_verified", "total_spent_usd", "jobs_posted", "hires", "hire_rate",
                                         "rating", "feedback_count", "member_since", "country")}
    public["past_contracts"] = [{k: c.get(k) for k in ("title", "total_paid_usd", "hours")}
                                for c in client.get("past_contracts", [])[:5]]
    rep, u = await to_thread(structured, "risk_assessor", RiskReport, {
        "signals": {"flags": flags, "client_quality": info["client_quality"],
                    "injection_detected": info["injection"]},
        "client": public, "job_post": wrap("job_post", f"{j['title']}\n\n{j['description']}\n\nScreening questions:\n"
                                                       + "\n".join(j.get("screening_questions") or []))})
    rep.score = round(max(rep.score, floor), 3)
    rep.flags = sorted(set(rep.flags) | set(flags))
    rep.client_quality = info["client_quality"]
    rep.injection_detected = rep.injection_detected or info["injection"]
    if info["injection"] and not rep.injection_excerpt:
        rep.injection_excerpt = (info["injection_regex"] or "")[:300] or None
    if rep.injection_detected and "injected_instructions" not in rep.flags:
        rep.flags.append("injected_instructions")
    return {"risk_report": rep.model_dump(), "usage": usage_entry("risk_assessor", u)}
