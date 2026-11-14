"""Client Liaison: read the client's latest message, classify it, draft a reply (with interview
slots when asked), and send it only after the human approves."""
from __future__ import annotations

import hashlib
from typing import Literal

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.guards import wrap
from agent.handoffs import ApprovalDecision
from agent.llm import structured
from agent.nodes.base import node, to_thread, tools, usage_entry
from agent.nodes.compliance import check


class Intent(BaseModel):
    intent: Literal["question", "negotiation", "interview_request", "scam_signal", "other"]
    summary: str
    asks: list[str] = Field(default_factory=list, max_length=3)


class Reply(BaseModel):
    text: str


def _history(msgs: list[dict]) -> str:
    return "\n".join(f"{m['sender']}: {m['text']}" for m in msgs[-8:])


@node("liaison_read", budget_key="max_tokens_per_conversation_turn")
async def read(state: dict) -> dict:
    msgs = (await tools().call("client_liaison", "list_messages", {"proposal_id": state["proposal_id"]}))["messages"]
    if not msgs or msgs[-1]["sender"] != "client":
        return {"messages": msgs, "status": "done", "status_reason": "no new client message"}
    props = (await tools().call("client_liaison", "list_proposals", {}))["proposals"]
    prop = next(p for p in props if p["proposal_id"] == state["proposal_id"])
    job = await tools().call("client_liaison", "get_job", {"job_id": prop["job_id"]})
    return {"messages": msgs, "proposal": prop, "job": job, "status": "running"}


@node("liaison_intent", budget_key="max_tokens_per_conversation_turn")
async def classify(state: dict) -> dict:
    msgs = state["messages"]
    it, u = await to_thread(structured, "liaison_intent", Intent, {
        "status": state["proposal"]["status"], "history": wrap("conversation", _history(msgs[:-1]) or "(none)"),
        "latest": wrap("client_message", msgs[-1]["text"])})
    return {"intent": it.model_dump(), "usage": usage_entry("liaison_intent", u)}


@node("liaison_slots", budget_key="max_tokens_per_conversation_turn")
async def slots(state: dict) -> dict:
    tz = state.get("user", {}).get("timezone", "Asia/Karachi")
    r = await tools().call("client_liaison", "get_free_slots", {"days": 5, "duration_minutes": 30, "max_slots": 3,
                                                                 "timezone": tz})
    return {"slots": r["slots"]}


@node("client_liaison", budget_key="max_tokens_per_conversation_turn")
async def draft_reply(state: dict) -> dict:
    it, prop, user = state["intent"], state["proposal"], state.get("user", {})
    ev_text = "none"
    if it["intent"] in ("question", "negotiation", "other") and it.get("asks"):
        hits = (await tools().call("client_liaison", "search_evidence",
                                   {"requirement": "; ".join(it["asks"])[:500], "k": 3,
                                    "filters": {"exclude_types": ["proposal"]}}))["hits"]
        ev_text = "\n".join(f"[{h['evidence_id']}] {h['title']}: {h['snippet']}" for h in hits) or "none"
    floor = user.get("hourly_rate") if prop["bid_type"] == "hourly" else user.get("min_fixed_budget")
    r, u = await to_thread(structured, "client_liaison", Reply, {
        "floor": floor or prop["bid_amount"], "slots": [human_slot(s["start"]) for s in state.get("slots", [])] or "none",
        "intent": it["intent"], "asks": it.get("asks", []), "job_title": state["job"]["title"],
        "bid": f"${prop['bid_amount']:g} {prop['bid_type']}", "evidence": ev_text,
        "history": wrap("conversation", _history(state["messages"]))})
    return {"reply": {"text": r.text}, "usage": usage_entry("client_liaison", u)}


@node("liaison_compliance", budget_key="max_tokens_per_conversation_turn")
async def reply_compliance(state: dict) -> dict:
    v, u = await check(state["reply"]["text"], None)
    out = {"compliance": v.model_dump(), "reply": {**state["reply"], "text": v.cleaned_text or state["reply"]["text"]},
           "usage": usage_entry("liaison_compliance", {k: u.get(k, 0) for k in ("tokens_in", "tokens_out",
                                                                               "cost_usd", "calls")})}
    if not v.allowed:
        out.update(status="blocked", status_reason=f"Compliance Guard blocked the reply: {v.reason}")
    return out


@node("liaison_approval", budget_key="max_tokens_per_conversation_turn")
async def approval(state: dict) -> dict:
    preview = await tools().call("client_liaison", "send_message", {
        "proposal_id": state["proposal_id"], "text": state["reply"]["text"],
        "idempotency_key": _key(state["thread_id"], "preview", str(len(state["messages"]))), "dry_run": True})
    raw = interrupt({"kind": "message", "thread_id": state["thread_id"], "proposal_id": state["proposal_id"],
                     "job": {"job_id": state["job"]["job_id"], "title": state["job"]["title"]},
                     "client_message": state["messages"][-1], "intent": state["intent"], "reply": state["reply"],
                     "slots": state.get("slots", []), "compliance": state["compliance"], "preview": preview})
    dec = ApprovalDecision.model_validate(raw)
    out: dict = {"approvals": [dec.model_dump()]}
    if dec.action == "reject":
        out.update(status="rejected", status_reason=f"Rejected by you: {dec.reason}")
    elif dec.action == "edit" and dec.edited_text:
        out["reply"] = {**state["reply"], "text": dec.edited_text, "edited": True}
    return out


@node("liaison_send", budget_key="max_tokens_per_conversation_turn")
async def send(state: dict) -> dict:
    n = str(len(state["messages"]))
    if state["reply"].get("edited"):
        v, _ = await check(state["reply"]["text"], None)       # edits are re-checked before sending
        if not v.allowed:
            return {"status": "blocked", "status_reason": f"Your edit breaks policy: {v.reason}"}
    res = await tools().call("executor", "send_message", {
        "proposal_id": state["proposal_id"], "text": state["reply"]["text"],
        "idempotency_key": _key(state["thread_id"], "send", n), "dry_run": False})
    hold = {}
    if state["intent"]["intent"] == "interview_request" and state.get("slots"):
        s = state["slots"][0]
        hold = await tools().call("executor", "create_tentative_hold", {
            "start": s["start"], "duration_minutes": 30, "title": f"Interview: {state['job']['title'][:80]}",
            "idempotency_key": _key(state["thread_id"], "hold", n)})
    return {"sent": res, "hold": hold, "status": "replied", "status_reason": "reply sent"}


def human_slot(start: str, minutes: int = 30) -> str:
    """'2026-11-16T09:00:00+05:00' -> 'Mon 16 Nov, 09:00-09:30 (UTC+5)'. Raw ISO strings reached
    clients in the first UI test of the reply flow."""
    import datetime as dt
    t = dt.datetime.fromisoformat(start)
    off = t.utcoffset() or dt.timedelta(0)
    h, m = divmod(abs(int(off.total_seconds())) // 60, 60)
    tz = f"UTC{'+' if off >= dt.timedelta(0) else '-'}{h}" + (f":{m:02d}" if m else "")
    return f"{t:%a} {t.day} {t:%b}, {t:%H:%M}-{t + dt.timedelta(minutes=minutes):%H:%M} ({tz})"


def _key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:40]
