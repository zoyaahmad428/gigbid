"""GigBid API: the web app's only backend.

    uvicorn apps.api.main:app --port 8000          (make api)

Everything the freelancer sees goes through here. Reads (jobs, proposals, messages, Connects) go
straight to the marketplace adapter with the user's own token. Anything that acts on the
marketplace goes through a graph thread, which always stops at a human approval first; this API
can only resume that approval with the human's decision, never send on its own.

Auth: email + password, scrypt-hashed; a random bearer token per session (only its hash is
stored). The SSE endpoint also accepts ?token= because EventSource cannot set headers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agent.handoffs import ApprovalDecision
from agent.marketplace_port import JobQuery, MarketplaceError, make_adapter
from agent.memory import store as memory
from agent.runtime import Runtime, bid_thread, convo_thread
from agent.settings import ROOT, config
from agent.trace import TRACES
from apps.api.runs import FINAL, RunManager
from apps.api.schemas import Credentials, Decision, Session, Settings, StartRun
from apps.api.store import AppStore

log = logging.getLogger("gigbid.api")
PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
DEFAULT_TOKENS = {"ai_ml": "dev-token-ai-ml", "fullstack": "dev-token-fullstack",
                  "data_analyst": "dev-token-data-analyst"}       # GigSim's dev accounts (gigsim/config.yaml)
WEB_DIST = ROOT / "apps/web/dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = AppStore()
    n = app.state.store.mark_orphans()
    if n:
        log.warning("%d run(s) were interrupted by the last shutdown; they can be retried", n)
    async with Runtime() as rt:
        app.state.rt = rt
        app.state.runs = RunManager(rt, app.state.store)
        app.state.adapters = {}
        yield
        for a in app.state.adapters.values():
            await a.aclose()


app = FastAPI(title="GigBid API", version="0.5.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ dependencies
def store(request: Request) -> AppStore:
    return request.app.state.store


def runs(request: Request) -> RunManager:
    return request.app.state.runs


def current_user(request: Request, authorization: str | None = Header(None), token: str | None = Query(None)) -> dict:
    tok = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else token
    user = request.app.state.store.session_user(tok) if tok else None
    if not user:
        raise HTTPException(401, "sign in first")
    return user


def onboarded(user: dict = Depends(current_user)) -> dict:
    if not user["onboarded"]:
        raise HTTPException(409, "finish onboarding first")
    return user


def agent_user(user: dict) -> dict:
    """The UserContext the graphs receive (agent/state.py)."""
    s = user["settings"]
    return {"user_id": user["user_id"], "persona": s["persona"], "marketplace_token": s["marketplace_token"],
            "name": s["name"], "headline": s.get("headline", ""), "hourly_rate": s["hourly_rate"],
            "min_fixed_budget": s["min_fixed_budget"], "autonomy_auto_skip": s.get("autonomy_auto_skip", True),
            "timezone": s.get("timezone", "Asia/Karachi")}


async def market(request: Request, user: dict, method: str, *args, retries: int = 3):
    """Call the user's marketplace adapter, retrying rate limits and transient 5xx (GigSim has chaos on)."""
    tok = user["settings"]["marketplace_token"]
    adapters = request.app.state.adapters
    if tok not in adapters:
        adapters[tok] = make_adapter(tok)
    for i in range(retries):
        try:
            return await getattr(adapters[tok], method)(*args)
        except MarketplaceError as e:
            if e.kind == "not_found":
                raise HTTPException(404, str(e)) from None
            if not e.retryable or i == retries - 1:
                raise HTTPException(502, f"marketplace: {e}") from None
            await asyncio.sleep(0.5 * (i + 1))


def own_run(thread_id: str, user: dict, st: AppStore) -> dict:
    r = st.run(thread_id)
    if not r or r["user_id"] != user["user_id"]:
        raise HTTPException(404, "no such run")
    return r


# ------------------------------------------------------------------ auth and onboarding
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "gigbid-api"}


@app.post("/api/auth/register", response_model=Session, status_code=201)
async def register(body: Credentials, st: AppStore = Depends(store)):
    try:
        u = st.create_user(body.email, body.password)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return {"token": st.new_session(u["user_id"]), "user": u}


@app.post("/api/auth/login", response_model=Session)
async def login(body: Credentials, st: AppStore = Depends(store)):
    tok = st.login(body.email, body.password)
    if not tok:
        raise HTTPException(401, "wrong email or password")
    return {"token": tok, "user": st.session_user(tok)}


@app.post("/api/auth/logout", status_code=204)
async def logout(authorization: str = Header(...), st: AppStore = Depends(store)):
    st.logout(authorization[7:])


@app.get("/api/me")
async def me(user: dict = Depends(current_user)):
    return user


@app.get("/api/personas")
async def personas():
    """The three portfolios the agents can draw evidence from (data/personas/)."""
    out = []
    for p in PERSONAS:
        prof = json.loads((ROOT / f"data/personas/{p}/profile.json").read_text())
        out.append({"persona": p, "name": prof["name"], "headline": prof["headline"], "category": prof["category"],
                    "skills": prof["core_skills"], "does_not_do": prof.get("does_not_do", []),
                    "hourly_rate": prof["hourly_rate_usd"], "min_fixed_budget": prof["min_fixed_budget_usd"],
                    "weekly_connects_budget": prof.get("weekly_connects_budget", 100),
                    "default_token": DEFAULT_TOKENS[p]})
    return out


@app.put("/api/me/settings")
async def save_settings(body: Settings, request: Request, user: dict = Depends(current_user),
                        st: AppStore = Depends(store)):
    probe = {**user, "settings": body.model_dump()}
    acct = await market(request, probe, "get_connects_balance")        # proves the token works
    if acct.get("persona") and acct["persona"] != body.persona:
        raise HTTPException(400, f"this marketplace token belongs to the {acct['persona']} account, "
                                 f"not {body.persona}")
    return st.save_settings(user["user_id"], body.model_dump(), onboarded=True)


# ------------------------------------------------------------------ jobs
@app.get("/api/account")
async def account(request: Request, user: dict = Depends(onboarded)):
    return await market(request, user, "get_connects_balance")


@app.get("/api/jobs")
async def jobs(request: Request, q: str | None = None, category: str | None = None,
               budget_type: str | None = None, cursor: int = 0, limit: int = Query(20, le=50),
               user: dict = Depends(onboarded), st: AppStore = Depends(store)):
    res = await market(request, user, "search_jobs",
                       JobQuery(q=q or None, category=category or None, budget_type=budget_type or None,
                                cursor=cursor, limit=limit))
    by_job = st.runs_by_job(user["user_id"], [j["job_id"] for j in res["jobs"]])
    for j in res["jobs"]:
        j["run"] = by_job.get(j["job_id"])
    return res


@app.get("/api/jobs/{job_id}")
async def job(job_id: str, request: Request, user: dict = Depends(onboarded), st: AppStore = Depends(store)):
    j = await market(request, user, "get_job", job_id)
    client = await market(request, user, "get_client_history", j["client_id"]) if j.get("client_id") else None
    return {"job": j, "client": client, "run": st.run(bid_thread(user["user_id"], job_id))}


@app.post("/api/jobs/{job_id}/run", status_code=202)
async def start_bid(job_id: str, body: StartRun, request: Request, user: dict = Depends(onboarded),
                    st: AppStore = Depends(store), rm: RunManager = Depends(runs)):
    tid = bid_thread(user["user_id"], job_id)
    existing = st.run(tid)
    if existing and not body.force_bid and existing["status"] not in ("failed",):
        raise HTTPException(409, f"this job already has a run ({existing['status']}); open it instead")
    j = await market(request, user, "get_job", job_id)
    au = agent_user(user)
    try:
        rm.launch(tid, au, "bid", rm.rt.start_bid(au, job_id, force_bid=body.force_bid), job_id=job_id,
                  title=j.get("title"))
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return {"thread_id": tid, "status": "running"}


# ------------------------------------------------------------------ runs, approvals and the live stream
@app.get("/api/runs")
async def list_runs(kind: str | None = None, status: str | None = None, user: dict = Depends(onboarded),
                    st: AppStore = Depends(store)):
    return {"runs": st.runs(user["user_id"], kind=kind, status=status)}


@app.get("/api/approvals")
async def approvals(user: dict = Depends(onboarded), st: AppStore = Depends(store)):
    """Pending approvals. Rebuilt from the run index; each one's payload comes from the checkpointer."""
    return {"runs": st.runs(user["user_id"], status="awaiting_approval")}


STATE_KEYS = ["job", "job_spec", "risk_report", "fit_report", "decision", "price_quote", "draft", "drafts",
              "verification", "critiques", "compliance", "preview", "approvals", "submission", "usage", "errors",
              "status", "status_reason", "steps", "revision",
              # conversation threads
              "proposal", "messages", "intent", "slots", "reply", "sent", "hold"]


@app.get("/api/runs/{thread_id}")
async def get_run(thread_id: str, user: dict = Depends(onboarded), st: AppStore = Depends(store),
                  rm: RunManager = Depends(runs)):
    r = own_run(thread_id, user, st)
    s = await rm.rt.state(thread_id)
    v = s["values"]
    return jsonable_encoder({
        "run": r, "busy": rm.busy(thread_id), "next": s["next"], "pending_approval": s["pending_approval"],
        "values": {k: v[k] for k in STATE_KEYS if k in v},
        "evidence": {k: {kk: e.get(kk) for kk in ("title", "type", "text", "skills")}
                     for k, e in (v.get("evidence") or {}).items()},
        "history": await rm.rt.history(thread_id), "trace": TRACES.events(thread_id),
        "totals": TRACES.totals(thread_id)})


@app.get("/api/runs/{thread_id}/stream")
async def stream(thread_id: str, request: Request, after: int = 0, last_event_id: str | None = Header(None),
                 user: dict = Depends(onboarded), st: AppStore = Depends(store), rm: RunManager = Depends(runs)):
    own_run(thread_id, user, st)
    seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

    async def gen():
        nonlocal seq
        idle = 0.0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            evs = st.events(thread_id, seq)
            for ev in evs:
                seq = ev["seq"]
                yield f"id: {seq}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            if evs:
                idle = 0.0
            r = st.run(thread_id)
            if not rm.busy(thread_id) and r and r["status"] in FINAL and not st.events(thread_id, seq):
                yield f"event: end\ndata: {json.dumps({'status': r['status']})}\n\n"
                return
            idle += 0.4
            if idle >= 15:                               # keep proxies from closing a quiet stream
                yield ": ping\n\n"
                idle = 0.0
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/runs/{thread_id}/decision", status_code=202)
async def decide(thread_id: str, body: Decision, user: dict = Depends(onboarded), st: AppStore = Depends(store),
                 rm: RunManager = Depends(runs)):
    r = own_run(thread_id, user, st)
    try:
        dec = ApprovalDecision.model_validate(body.model_dump())
    except ValidationError as e:
        raise HTTPException(422, e.errors()[0]["msg"]) from None
    s = await rm.rt.state(thread_id)
    if not s["pending_approval"]:
        raise HTTPException(409, "nothing is waiting for approval on this run")
    au = agent_user(user)
    try:
        rm.launch(thread_id, au, r["kind"], rm.rt.resume(thread_id, au, dec.model_dump(exclude_none=True)))
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return {"thread_id": thread_id, "status": "running"}


@app.post("/api/runs/{thread_id}/retry", status_code=202)
async def retry(thread_id: str, user: dict = Depends(onboarded), st: AppStore = Depends(store),
                rm: RunManager = Depends(runs)):
    r = own_run(thread_id, user, st)
    if r["status"] not in ("degraded", "dead_letter", "failed", "interrupted", "needs_attention"):
        raise HTTPException(409, f"a {r['status']} run cannot be retried")
    au = agent_user(user)
    gen = rm.rt.retry(thread_id, au)
    try:
        first = await gen.__anext__()               # surfaces "nothing to retry" as a 409, not a failed run
    except LookupError as e:
        raise HTTPException(409, str(e)) from None
    except StopAsyncIteration:
        raise HTTPException(409, "nothing to retry") from None

    async def chain():
        yield first
        async for ev in gen:
            yield ev
    rm.launch(thread_id, au, r["kind"], chain())
    return {"thread_id": thread_id, "status": "running"}


# ------------------------------------------------------------------ proposals and messages
@app.get("/api/proposals")
async def proposals(request: Request, user: dict = Depends(onboarded), st: AppStore = Depends(store)):
    res = await market(request, user, "list_proposals", None)
    by_job = st.runs_by_job(user["user_id"], [p["job_id"] for p in res["proposals"]])
    convos = {}
    for r in st.runs(user["user_id"], kind="convo"):
        convos.setdefault(r["proposal_id"], r)                  # newest first
    for p in res["proposals"]:
        p["run"] = by_job.get(p["job_id"])
        p["reply_run"] = convos.get(p["proposal_id"])
    return res


@app.get("/api/proposals/{proposal_id}")
async def proposal(proposal_id: str, request: Request, user: dict = Depends(onboarded),
                   st: AppStore = Depends(store)):
    p = await market(request, user, "get_proposal", proposal_id)
    p["reply_runs"] = [r for r in st.runs(user["user_id"], kind="convo") if r["proposal_id"] == proposal_id]
    return p


@app.post("/api/proposals/{proposal_id}/reply", status_code=202)
async def draft_reply(proposal_id: str, request: Request, user: dict = Depends(onboarded),
                      st: AppStore = Depends(store), rm: RunManager = Depends(runs)):
    """Start the conversation graph on the latest client message; it stops at approval before sending."""
    msgs = (await market(request, user, "list_messages", proposal_id))["messages"]
    if not msgs or msgs[-1].get("sender") == "freelancer":
        raise HTTPException(409, "there is no new client message to answer")
    tid = convo_thread(user["user_id"], proposal_id, len(msgs))
    if st.run(tid) and st.run(tid)["status"] != "failed":
        return {"thread_id": tid, "status": st.run(tid)["status"]}
    p = await market(request, user, "get_proposal", proposal_id)
    bid = st.run(bid_thread(user["user_id"], p["job_id"]))
    au = agent_user(user)
    try:
        rm.launch(tid, au, "convo", rm.rt.start_conversation(au, proposal_id, len(msgs)), job_id=p.get("job_id"),
                  proposal_id=proposal_id, title=(bid or {}).get("title") or p["job_id"])
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return {"thread_id": tid, "status": "running"}


# ------------------------------------------------------------------ analytics, memory, config
@app.get("/api/analytics")
async def analytics(request: Request, user: dict = Depends(onboarded), st: AppStore = Depends(store)):
    bids = st.runs(user["user_id"], kind="bid", limit=10000)
    convos = st.runs(user["user_id"], kind="convo", limit=10000)
    # only proposals GigBid submitted for this user: a marketplace account can hold others
    mine = {r["summary"].get("proposal_id") for r in bids} - {None}
    props = [p for p in (await market(request, user, "list_proposals", None))["proposals"]
             if p["proposal_id"] in mine]
    acct = await market(request, user, "get_connects_balance")
    status = Counter(r["status"] for r in bids)
    decided = status["submitted"] + status["rejected"]
    cost = [r["summary"].get("trace", {}).get("cost_usd", 0) for r in bids + convos]
    tokens = [sum(r["summary"].get("trace", {}).get(k, 0) for k in ("tokens_in", "tokens_out")) for r in bids]
    pstat = Counter(p["status"] for p in props)
    sent = [p for p in props if p["status"] != "draft"]
    engaged = sum(pstat[s] for s in ("replied", "interview", "hired"))
    days = Counter(time.strftime("%Y-%m-%d", time.gmtime(r["created_at"])) for r in bids)
    skip_reasons = Counter((r["reason"] or "").split(";")[0][:90] for r in bids if r["status"] in ("skipped", "watching"))
    fits = [r["summary"].get("fit") for r in bids if r["summary"].get("fit") is not None]
    return {
        "runs": {"bid": len(bids), "conversation": len(convos), "by_status": dict(status)},
        "approval": {"submitted": status["submitted"], "rejected": status["rejected"],
                     "approval_rate": round(status["submitted"] / decided, 3) if decided else None,
                     "edited_before_approval": sum(r["summary"].get("edits", 0) > 0 for r in bids)},
        "proposals": {"by_status": dict(pstat), "sent": len(sent),
                      "response_rate": round(engaged / len(sent), 3) if sent else None,
                      "hired": pstat["hired"]},
        "connects": {"balance": acct.get("connects_balance"), "weekly_allowance": acct.get("weekly_allowance"),
                     "spent": sum(p.get("connects_spent") or 0 for p in sent)},
        "cost": {"total_usd": round(sum(cost), 4), "per_run_usd": round(sum(cost) / len(cost), 5) if cost else None,
                 "tokens_per_bid_run": round(sum(tokens) / len(tokens)) if tokens else None},
        "fit": {"mean": round(sum(fits) / len(fits), 3) if fits else None,
                "histogram": Counter(min(9, int(f * 10)) for f in fits)},
        "skip_reasons": skip_reasons.most_common(6),
        "runs_per_day": sorted(days.items()),
    }


@app.get("/api/memory")
async def get_memory(user: dict = Depends(onboarded)):
    """What the agents have learned about this user (style rules from edits, lessons from outcomes)."""
    uid = user["user_id"]
    lessons = {}
    for item in memory.get_store().search(("outcome", uid), limit=100):
        lessons[item.key] = item.value.get("lessons", [])
    return {"style_rules": memory.style_rules(uid), "outcome_lessons": lessons}


@app.delete("/api/memory/style/{rule:path}", status_code=204)
async def forget_style(rule: str, user: dict = Depends(onboarded)):
    memory.delete_style_rule(user["user_id"], rule)


@app.delete("/api/memory/outcome/{category}", status_code=204)
async def forget_outcome(category: str, user: dict = Depends(onboarded)):
    memory.delete_outcome_lessons(user["user_id"], category)


@app.get("/api/config")
async def agent_config(user: dict = Depends(current_user)):
    """Read-only: the thresholds and budgets the agents use (agent/config.yaml)."""
    c = config()
    return {k: c.get(k) for k in ("thresholds", "pricing", "budgets", "models", "autonomy", "memory")}


# ------------------------------------------------------------------ the built web app (production)
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        f = WEB_DIST / path
        return FileResponse(f if path and f.is_file() else WEB_DIST / "index.html")
