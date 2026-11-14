"""Proposal Writer: cover letter and screening answers, every claim cited to evidence."""
from __future__ import annotations

from pydantic import BaseModel, Field

from agent.guards import wrap
from agent.handoffs import ProposalDraft
from agent.llm import structured
from agent.memory.store import outcome_lessons, style_rules
from agent.nodes.base import node, to_thread, usage_entry
from agent.settings import config


class WriterOut(BaseModel):
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)


def evidence_block(evidence: dict) -> str:
    parts = []
    for eid, d in evidence.items():
        text = d["text"] if len(d["text"]) < 1800 else d["text"][:1800] + " ..."
        parts.append(f"[{eid}] ({d['type']}) {d['title']}\n{text}")
    return "\n\n".join(parts) or "(no evidence: do not claim relevant experience)"


@node("writer")
async def writer(state: dict) -> dict:
    j, user, th = state["job"], state.get("user", {}), config()["thresholds"]
    rev = state.get("revision", 0)
    notes = ""
    if state.get("critiques"):
        c = state["critiques"][-1]
        notes += "REVISION REQUESTED. Fix these issues:\n- " + "\n- ".join(c["issues"]) + "\n"
    removed = [r["sentence"] for r in state.get("verification", {}).get("results", []) if r["label"] == "unsupported"]
    if removed:
        notes += "These sentences were removed as unsupported; do not repeat them:\n- " + "\n- ".join(removed[:6])
    q = state["price_quote"]
    uid = user.get("user_id", "local")
    out, u = await to_thread(structured, "writer", WriterOut, {
        "min_words": th["proposal_min_words"], "max_words": th["proposal_max_words"],
        "style_rules": [r["text"] for r in style_rules(uid)] or "none yet",
        "outcome_lessons": [x["text"] for x in outcome_lessons(uid, j.get("category") or "Other")] or "none yet",
        "persona_name": user.get("name", "the freelancer"), "headline": user.get("headline", ""),
        "bid": f"${q['amount']:g} {'per hour' if q['bid_type'] == 'hourly' else 'fixed'}",
        "job_spec": state.get("job_spec", {}), "screening": j.get("screening_questions") or "none",
        "evidence": evidence_block(state.get("evidence", {})), "revision_notes": notes,
        "job_post": wrap("job_post", f"{j['title']}\n\n{j['description']}")})
    answers = list(out.screening_answers)[: len(j.get("screening_questions") or [])]
    draft = ProposalDraft(job_id=j["job_id"], cover_letter=out.cover_letter.strip(), screening_answers=answers,
                          evidence_ids=list(state.get("evidence", {})), revision=rev, status="drafted")
    return {"draft": draft.model_dump(), "drafts": [{**draft.model_dump(), "stage": "written"}],
            "usage": usage_entry("writer", u)}
