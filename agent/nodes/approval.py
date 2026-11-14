"""Human approval (R4, C5) and the executor that acts on it.

`approval_gate` builds the approval payload (draft, the evidence behind every
claim, risk report, price rationale and the marketplace's dry-run preview of
exactly what would be sent and what it costs), then calls interrupt(). The graph
stops here and is checkpointed; it resumes only when the UI sends an
ApprovalDecision. A refresh or a server restart never loses the pending approval.

`submit` runs only after "approve". Its idempotency keys are derived from the
thread id, so if the process dies mid-submit and the thread is replayed, the
Marketplace server returns the first result instead of submitting again.
"""
from __future__ import annotations

import hashlib

from langgraph.types import interrupt

from agent.handoffs import ApprovalDecision
from agent.mcp_client import ToolCallError
from agent.memory.store import record_edit
from agent.nodes.base import node, tools


def _key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:40]


def _letter(d: dict) -> str:
    return d["cover_letter"]


@node("approval_gate")
async def approval_gate(state: dict) -> dict:
    d, q, j = state["draft"], state["price_quote"], state["job"]
    preview = await tools().call("executor", "create_proposal_draft", {
        "job_id": j["job_id"], "cover_letter": _letter(d), "bid_amount": q["amount"], "bid_type": q["bid_type"],
        "screening_answers": d.get("screening_answers", []), "idempotency_key": _key(state["thread_id"], "preview"),
        "dry_run": True})
    ev = state.get("evidence", {})
    payload = {
        "kind": "proposal", "thread_id": state["thread_id"], "job": {k: j.get(k) for k in ("job_id", "title", "client_id")},
        "draft": d, "claims": [{**c, "evidence": [{"evidence_id": e, "title": ev.get(e, {}).get("title"),
                                                  "type": ev.get(e, {}).get("type")} for e in c["evidence_ids"]]}
                               for c in d.get("citations", [])],
        "verification": state.get("verification", {}).get("stats"),
        "critiques": state.get("critiques", []), "compliance": state.get("compliance"),
        "risk_report": state["risk_report"], "fit_report": state["fit_report"], "price_quote": q,
        "preview": preview, "decision": state["decision"],
    }
    raw = interrupt(payload)                          # <- graph pauses here until the human decides
    dec = ApprovalDecision.model_validate(raw)
    out: dict = {"preview": preview, "approvals": [dec.model_dump()]}
    uid = state.get("user", {}).get("user_id", "local")
    if dec.action == "reject":
        out.update(status="rejected", status_reason=f"Rejected by you: {dec.reason}")
    elif dec.action == "edit":
        nd = dict(d)
        if dec.edited_text:
            learned = record_edit(uid, _letter(d), dec.edited_text, thread_id=state["thread_id"])
            out["approvals"][0]["learned"] = learned
            nd["cover_letter"] = dec.edited_text
        if dec.edited_bid:
            out["price_quote"] = {**q, "amount": dec.edited_bid,
                                  "rationale": q["rationale"] + [f"edited by you to ${dec.edited_bid:g}"]}
        nd["status"] = "pending_review"
        out.update(draft=nd, after_edit=True, drafts=[{**nd, "stage": "edited_by_user"}])
    else:
        out.update(status="running", status_reason="approved")
    return out


def route_after_gate(state: dict) -> str:
    last = state["approvals"][-1]["action"] if state.get("approvals") else None
    if state.get("status") in ("rejected", "needs_attention", "dead_letter", "degraded"):
        return "end"
    return {"approve": "submit", "edit": "verifier"}.get(last, "end")


@node("submit")
async def submit(state: dict) -> dict:
    d, q, j, tid = state["draft"], state["price_quote"], state["job"], state["thread_id"]
    content = _key(_letter(d), str(q["amount"]), "|".join(d.get("screening_answers", [])))
    try:
        created = await tools().call("executor", "create_proposal_draft", {
            "job_id": j["job_id"], "cover_letter": _letter(d), "bid_amount": q["amount"], "bid_type": q["bid_type"],
            "screening_answers": d.get("screening_answers", []), "idempotency_key": _key(tid, "draft", content),
            "dry_run": False})
        pid = created["result"]["proposal_id"]
    except ToolCallError as e:
        if e.kind != "conflict":
            raise
        mine = await tools().call("executor", "list_proposals", {})
        pid = next(p["proposal_id"] for p in mine["proposals"] if p["job_id"] == j["job_id"])
    res = await tools().call("executor", "submit_proposal", {"proposal_id": pid, "idempotency_key": _key(tid, "submit"),
                                                             "dry_run": False})
    return {"submission": res, "status": "submitted",
            "status_reason": f"Submitted; {res.get('connects_cost', 0)} Connects spent"
                             + (" (replayed: already submitted)" if res.get("idempotent_replay") else ""),
            "drafts": [{**d, "stage": "submitted"}]}
