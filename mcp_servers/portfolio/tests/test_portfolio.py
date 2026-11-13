"""Portfolio MCP server tests through an in-memory MCP client."""
from __future__ import annotations

import httpx
from mcp import Client

from mcp_servers.common import User, bind
from mcp_servers.portfolio.server import http_app, mcp


async def test_search_returns_typed_evidence():
    async with Client(mcp) as c:
        r = await c.call_tool("search_evidence", {"requirement": "RAG over support documentation", "k": 3,
                                                  "filters": {"exclude_types": ["proposal"]}})
    hits = r.structured_content["hits"]
    assert not r.is_error and 1 <= len(hits) <= 3
    assert {"evidence_id", "type", "source", "date", "outcome", "snippet"} <= set(hits[0])
    assert all(h["type"] != "proposal" for h in hits)


async def test_implied_skill_found_in_code_only():
    async with Client(mcp) as c:
        r = await c.call_tool("search_evidence", {"requirement": "Celery task queue", "k": 3})
    assert r.structured_content["hits"][0]["type"] == "code"


async def test_filters_outcome_and_type():
    async with Client(mcp) as c:
        r = await c.call_tool("search_evidence", {"requirement": "machine learning", "k": 10,
                                                  "filters": {"types": ["proposal"], "outcomes": ["won"]}})
    hits = r.structured_content["hits"]
    assert hits and all(h["type"] == "proposal" and h["outcome"] == "won" for h in hits)


async def test_case_study_resources_and_prompts():
    async with Client(mcp) as c:
        hit = (await c.call_tool("search_evidence", {"requirement": "demand forecasting", "k": 1,
                                                     "filters": {"types": ["case_study"]}})).structured_content["hits"][0]
        cs = (await c.call_tool("get_case_study", {"id": hit["evidence_id"]})).structured_content
        assert cs["markdown"].startswith("#") and cs["related"]
        text = (await c.read_resource(f"portfolio://case/{hit['evidence_id']}")).contents[0].text
        assert text == cs["markdown"]
        templates = {t.uri_template for t in (await c.list_resource_templates()).resource_templates}
        assert {"portfolio://case/{case_id}", "portfolio://proposal/{proposal_id}"} <= templates
        p = await c.get_prompt("answer_screening_question", {"question": "Have you built RAG before?"})
        assert "search_evidence" in p.messages[0].content.text
        missing = await c.call_tool("get_case_study", {"id": "nope"})
        assert missing.is_error


async def test_tenant_scoping():
    bind(User(user_id="u_data", persona="data_analyst"))
    try:
        async with Client(mcp) as c:
            skills = {s["skill"] for s in (await c.call_tool("list_skills", {})).structured_content["skills"]}
        assert "Power BI" in skills and "PyTorch" not in skills
    finally:
        bind(User(user_id="local", persona="ai_ml"))


async def test_http_requires_bearer_token():
    app = http_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as h:
        assert (await h.post("/mcp", json={})).status_code == 401
        assert (await h.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await h.get("/health")).status_code == 200
