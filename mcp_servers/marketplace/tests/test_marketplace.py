"""Marketplace MCP server tests: in-memory MCP client -> server -> GigSim adapter -> in-process GigSim."""
from __future__ import annotations

import importlib
import json

import httpx
import pytest
from mcp import Client

AI_TOKEN = "dev-token-ai-ml"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mkt")
    mp = pytest.MonkeyPatch()
    mp.setenv("GIGSIM_STATE_DB", str(tmp / "gigsim.sqlite3"))
    mp.setenv("GIGSIM_CHAOS", "0")
    mp.setenv("MARKETPLACE_AUDIT_PATH", str(tmp / "audit.jsonl"))
    mp.setenv("MARKETPLACE_IDEM_DB", str(tmp / "idem.sqlite3"))
    mp.setenv("GIGSIM_TOKEN", AI_TOKEN)
    import gigsim.app as gs
    gs = importlib.reload(gs)
    gs.BUCKET.burst = 10_000
    import mcp_servers.marketplace.server as srv
    srv = importlib.reload(srv)
    srv.ADAPTER_OVERRIDES["transport"] = httpx.ASGITransport(app=gs.app)
    srv._adapters.clear()
    yield gs, srv
    mp.undo()


def _dev_job(gs, scam=False):
    with open("evals/datasets/dev_ai_ml.jsonl") as f:
        for line in f:
            j = json.loads(line)["job"]
            if gs.DATA.hidden[j["client_id"]]["is_scammer"] == scam and not j["screening_questions"]:
                return j


LETTER = ("I built a retrieval assistant over support documents for a media client: it cut handling time "
          "by 33% and reached 86% answer accuracy on a 200-question test set. I would start by agreeing how "
          "to measure answers, then ship a prototype in week one.")


async def test_tools_listed_with_annotations(env):
    _, srv = env
    async with Client(srv.mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"search_jobs", "get_job", "get_client_history", "get_connects_balance", "list_messages",
            "create_proposal_draft", "submit_proposal", "send_message"} <= set(tools)
    assert tools["search_jobs"].annotations.read_only_hint is True
    assert tools["submit_proposal"].annotations.destructive_hint is True
    assert tools["submit_proposal"].annotations.idempotent_hint is True
    assert tools["submit_proposal"].input_schema["properties"]["dry_run"]["default"] is True
    assert all(tools[n].output_schema for n in tools)


async def test_read_tools(env):
    gs, srv = env
    gs.STATE.set_clock(gs.parse("2026-11-08T00:00:00Z"))
    async with Client(srv.mcp) as c:
        page = (await c.call_tool("search_jobs", {"limit": 5})).structured_content
        assert page["jobs"] and "provenance" not in page["jobs"][0]
        job = (await c.call_tool("get_job", {"job_id": page["jobs"][0]["job_id"]})).structured_content
        cl = (await c.call_tool("get_client_history", {"client_id": job["client_id"]})).structured_content
        assert "must_have_skills" not in cl
        bal = (await c.call_tool("get_connects_balance", {})).structured_content
        assert bal["connects_balance"] >= 0
        st = (await c.call_tool("marketplace_status", {})).structured_content
        assert st["available"] and st["mode"] == "gigsim"
        bad = await c.call_tool("get_job", {"job_id": "job_does_not_exist"})
        assert bad.is_error and "[not_found]" in bad.content[0].text


async def test_dry_run_then_exactly_once_submit(env):
    gs, srv = env
    j = _dev_job(gs)
    async with Client(srv.mcp) as c:
        d = (await c.call_tool("create_proposal_draft", {
            "job_id": j["job_id"], "cover_letter": LETTER, "bid_amount": 200, "bid_type": j["budget"]["type"],
            "idempotency_key": "draft-0001"})).structured_content
        pid = d["result"]["proposal_id"]
        bal0 = (await c.call_tool("get_connects_balance", {})).structured_content["connects_balance"]

        pv = (await c.call_tool("submit_proposal", {"proposal_id": pid, "idempotency_key": "submit-0001"})).structured_content
        assert pv["dry_run"] is True and pv["connects_cost"] == j["connects_cost"]
        assert pv["would_send"]["cover_letter"] == LETTER
        assert (await c.call_tool("get_connects_balance", {})).structured_content["connects_balance"] == bal0

        r1 = (await c.call_tool("submit_proposal", {"proposal_id": pid, "idempotency_key": "submit-0002",
                                                    "dry_run": False})).structured_content
        r2 = (await c.call_tool("submit_proposal", {"proposal_id": pid, "idempotency_key": "submit-0002",
                                                    "dry_run": False})).structured_content
        assert r1["result"]["status"] == "submitted" and r2["idempotent_replay"] is True
        assert (await c.call_tool("get_connects_balance", {})).structured_content["connects_balance"] == \
            bal0 - j["connects_cost"]
        again = await c.call_tool("submit_proposal", {"proposal_id": pid, "idempotency_key": "submit-0003",
                                                      "dry_run": False})
        assert again.is_error and "[conflict]" in again.content[0].text
    assert srv.AUDIT.verify()


async def test_dry_run_reports_policy_violations(env):
    gs, srv = env
    j = _dev_job(gs)
    async with Client(srv.mcp) as c:
        pv = (await c.call_tool("create_proposal_draft", {
            "job_id": j["job_id"], "cover_letter": LETTER + " Contact me at me@gmail.com, results guaranteed.",
            "bid_amount": 200, "bid_type": j["budget"]["type"], "idempotency_key": "draft-viol",
            "dry_run": True})).structured_content
    assert set(pv["checks"]["policy_violations"]) == {"contact_details", "guarantee"}


async def test_input_validation(env):
    _, srv = env
    async with Client(srv.mcp) as c:
        r = await c.call_tool("create_proposal_draft", {"job_id": "x", "cover_letter": "too short", "bid_amount": -1,
                                                        "bid_type": "fixed", "idempotency_key": "k"})
    assert r.is_error


async def test_marketplace_down_is_reported(env):
    _, srv = env

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused")

    saved = srv.ADAPTER_OVERRIDES.get("transport")
    srv.ADAPTER_OVERRIDES["transport"] = Down()
    srv._adapters.clear()
    srv.marketplace_config.__globals__  # noqa: B018
    try:
        import mcp_servers.marketplace.adapters.gigsim as g
        old = g.asyncio.sleep

        async def fast(_):
            return None
        g.asyncio.sleep = fast
        async with Client(srv.mcp) as c:
            st = (await c.call_tool("marketplace_status", {})).structured_content
            r = await c.call_tool("search_jobs", {})
        assert st["available"] is False and "unavailable" in st["error"]
        assert r.is_error and "[unavailable, retryable]" in r.content[0].text
    finally:
        g.asyncio.sleep = old
        srv.ADAPTER_OVERRIDES["transport"] = saved
        srv._adapters.clear()
