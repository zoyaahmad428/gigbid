"""API tests: auth, onboarding, run ownership, approval validation and the SSE replay.

The marketplace is a fake adapter and no graph is run, so no GigSim, model or LLM key is needed.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


class FakeMarket:
    def __init__(self, token: str):
        self.persona = {"tok-ai": "ai_ml", "tok-fs": "fullstack"}.get(token)

    async def get_connects_balance(self):
        from agent.marketplace_port import MarketplaceError
        if not self.persona:
            raise MarketplaceError("auth", "bad token")
        return {"persona": self.persona, "connects_balance": 80, "weekly_allowance": 80}

    async def search_jobs(self, q):
        return {"jobs": [{"job_id": "job_1", "title": "RAG chatbot"}], "total": 1, "next_cursor": None,
                "sim_time": "2026-11-12T00:00:00Z"}

    async def list_proposals(self, status):
        return {"proposals": []}

    async def get_job(self, job_id):
        return {"job_id": job_id, "title": "RAG chatbot"}

    async def aclose(self):
        pass


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGBID_DB", "sqlite")
    monkeypatch.setenv("GIGBID_APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("GIGBID_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite3"))
    import apps.api.main as main
    monkeypatch.setattr(main, "make_adapter", lambda tok: FakeMarket(tok))
    with TestClient(main.app) as c:
        yield c


SETTINGS = {"persona": "ai_ml", "name": "Sana", "hourly_rate": 55, "min_fixed_budget": 100,
            "marketplace_token": "tok-ai"}


def signup(c, email="a@example.com") -> dict:
    r = c.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201
    return {"authorization": f"Bearer {r.json()['token']}"}


def test_auth_and_onboarding_gate(client):
    assert client.get("/api/me").status_code == 401
    h = signup(client)
    assert client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrongpass1"}).status_code == 401
    assert client.get("/api/jobs", headers=h).status_code == 409             # not onboarded yet
    bad = client.put("/api/me/settings", headers=h, json={**SETTINGS, "marketplace_token": "tok-fs"})
    assert bad.status_code == 400 and "fullstack" in bad.json()["detail"]   # token belongs to another persona
    assert client.put("/api/me/settings", headers=h, json=SETTINGS).json()["onboarded"] is True
    assert client.get("/api/jobs", headers=h).json()["jobs"][0]["run"] is None


def test_runs_are_private_and_decisions_are_validated(client):
    import apps.api.main as main
    h = signup(client)
    client.put("/api/me/settings", headers=h, json=SETTINGS)
    other = signup(client, "b@example.com")
    client.put("/api/me/settings", headers=other, json=SETTINGS)
    me = client.get("/api/me", headers=h).json()["user_id"]
    tid = f"bid:{me}:job_1"
    main.app.state.store.upsert_run(tid, me, "bid", status="awaiting_approval", job_id="job_1", title="RAG chatbot")
    path = f"/api/runs/{tid}/decision"

    assert client.post(path, headers=other, json={"action": "approve"}).status_code == 404     # not theirs
    r = client.post(path, headers=h, json={"action": "reject"})                                 # no reason
    assert r.status_code == 422 and "reason" in r.json()["detail"]
    assert client.post(path, headers=h, json={"action": "edit"}).status_code == 422             # nothing edited
    # the checkpointer has no interrupt for this thread, so nothing can be resumed
    assert client.post(path, headers=h, json={"action": "approve"}).status_code == 409
    assert main.app.state.store.run(tid)["status"] == "awaiting_approval"                       # untouched


def test_stream_replays_stored_events_then_ends(client):
    import apps.api.main as main
    h = signup(client)
    client.put("/api/me/settings", headers=h, json=SETTINGS)
    me = client.get("/api/me", headers=h).json()["user_id"]
    tid = f"bid:{me}:job_1"
    st = main.app.state.store
    st.upsert_run(tid, me, "bid", status="running")
    for ev in ({"type": "started"}, {"type": "node", "node": "load_job"},
               {"type": "done", "status": "skipped"}):
        st.add_event(tid, ev)
    st.upsert_run(tid, me, "bid", status="skipped")
    token = h["authorization"][7:]
    body = client.get(f"/api/runs/{tid}/stream", params={"token": token}).text
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [e.get("type") for e in events[:3]] == ["started", "node", "done"]
    assert events[-1] == {"status": "skipped"}                                  # the closing "end" event
    later = client.get(f"/api/runs/{tid}/stream", params={"token": token, "after": 2}).text
    assert later.count("event: node") == 0 and "event: done" in later            # resume after seq 2


def test_orphaned_runs_are_marked_on_startup(tmp_path):
    from apps.api.store import AppStore
    st = AppStore(tmp_path / "x.sqlite3")
    st.upsert_run("bid:u:j", "u", "bid", status="running")
    assert st.mark_orphans() == 1
    assert st.run("bid:u:j")["status"] == "interrupted"


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, fn, *args, _job_id=None):
        self.jobs.append((fn, args, _job_id))

    async def aclose(self):
        pass


def test_with_the_worker_the_api_queues_runs_and_only_sends_the_user_id(client):
    import apps.api.main as main
    q = FakeQueue()
    main.app.state.runs.queue = q
    h = signup(client)
    client.put("/api/me/settings", headers=h, json=SETTINGS)
    me = client.get("/api/me", headers=h).json()["user_id"]
    r = client.post("/api/jobs/job_1/run", headers=h, json={})
    assert r.status_code == 202
    (fn, args, job_id), = q.jobs
    assert fn == "run_thread" and args[:4] == (f"bid:{me}:job_1", me, "bid", "start_bid")
    assert "tok-ai" not in repr(args)                                           # no marketplace token in Redis
    assert main.app.state.store.run(f"bid:{me}:job_1")["status"] == "queued"
    assert client.post("/api/jobs/job_1/run", headers=h, json={"force_bid": True}).status_code == 409   # still queued
    main.app.state.runs.queue = None


def test_closed_signup_seeded_reviewers_and_the_daily_run_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGBID_DB", "sqlite")
    monkeypatch.setenv("GIGBID_APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("GIGBID_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite3"))
    monkeypatch.setenv("GIGBID_SEED_USERS", "reviewer@example.com:review-pass-1:fullstack")
    import apps.api.main as main
    monkeypatch.setattr(main, "make_adapter", lambda tok: FakeMarket(tok))
    monkeypatch.setattr(main, "ALLOW_SIGNUP", False)
    monkeypatch.setitem(main.config()["budgets"], "max_runs_per_user_per_day", 1)
    with TestClient(main.app) as c:
        assert c.post("/api/auth/register", json={"email": "x@example.com", "password": "password123"}).status_code == 403
        r = c.post("/api/auth/login", json={"email": "reviewer@example.com", "password": "review-pass-1"})
        assert r.status_code == 200 and r.json()["user"]["onboarded"] is True
        assert r.json()["user"]["settings"]["persona"] == "fullstack"
        h = {"authorization": f"Bearer {r.json()['token']}"}
        me = r.json()["user"]["user_id"]
        main.app.state.store.upsert_run(f"bid:{me}:job_0", me, "bid", status="skipped", job_id="job_0")
        assert c.post("/api/jobs/job_1/run", headers=h, json={}).status_code == 429
