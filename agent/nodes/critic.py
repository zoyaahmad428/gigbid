"""Critic: grounding, personalisation, length and clarity. Sends a draft back at most twice."""
from __future__ import annotations

import re

from agent.handoffs import Critique
from agent.llm import structured
from agent.nodes.base import node, to_thread, usage_entry
from agent.settings import config

CLICHE = re.compile(r"\b(passionate|dear hiring manager|perfect fit|hard[- ]?working|high[- ]quality work|"
                    r"i am an expert)\b", re.I)


def rule_issues(draft: dict, job: dict, th: dict) -> list[str]:
    issues = []
    words = len(draft["cover_letter"].split())
    if words < th["proposal_min_words"]:
        issues.append(f"Too short ({words} words); add a concrete first step or proof point.")
    if words > th["proposal_max_words"]:
        issues.append(f"Too long ({words} words); cut to under {th['proposal_max_words']}.")
    m = CLICHE.search(draft["cover_letter"])
    if m:
        issues.append(f"Remove the cliché \"{m.group(0)}\".")
    if "?" not in draft["cover_letter"]:
        issues.append("End with one specific question for the client.")
    n_q = len(job.get("screening_questions") or [])
    if len(draft.get("screening_answers", [])) < n_q:
        issues.append(f"Answer all {n_q} screening questions.")
    return issues


@node("critic")
async def critic(state: dict) -> dict:
    d, th, rev = state["draft"], config()["thresholds"], state.get("revision", 0)
    c, u = await to_thread(structured, "critic", Critique, {
        "min_words": th["proposal_min_words"], "max_words": th["proposal_max_words"],
        "job_spec": state.get("job_spec", {}), "verification": state.get("verification", {}).get("stats", {}),
        "matrix": [{"requirement": m["requirement"][:160], "status": m["status"]}
                   for m in state.get("fit_report", {}).get("matrix", [])],
        "word_count": len(d["cover_letter"].split()), "draft": d["cover_letter"], "answers": d["screening_answers"]})
    rules = rule_issues(d, state["job"], th)
    c.issues = list(dict.fromkeys(rules + c.issues))[:6]
    if rules:
        c.verdict = "revise"
    c.revision = rev
    can_revise = rev < config()["budgets"]["max_revisions"]
    out = {"critiques": [c.model_dump()], "usage": usage_entry("critic", u)}
    if c.verdict == "revise" and can_revise:
        out["revision"] = rev + 1
    return out


def route_after_critic(state: dict) -> str:
    c = state["critiques"][-1]
    if state.get("status") in ("needs_attention", "dead_letter", "degraded"):
        return "end"
    return "writer" if c["verdict"] == "revise" and state.get("revision", 0) > c["revision"] else "compliance"
