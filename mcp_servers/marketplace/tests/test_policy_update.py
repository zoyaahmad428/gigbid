"""The change request: GigSim's 2 Nov 2026 policy update adds a flat submission fee.

GigSim runs with data/policy_pack/gigsim_policy_2026-11-02.yaml. The Marketplace server must report
the fee, and the Pricing node must count it in the Connects a bid costs, so the approval card and the
"worth the Connects" check match what the marketplace then charges.
"""
from __future__ import annotations

import importlib
import json

import httpx
import pytest
from mcp import Client

AI_TOKEN = "dev-token-ai-ml"
POLICY = "data/policy_pack/gigsim_policy_2026-11-02.yaml"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("policy")
    mp = pytest.MonkeyPatch()
    mp.setenv("GIGSIM_POLICY", POLICY)
    mp.setenv("GIGSIM_STATE_DB", str(tmp / "gigsim.sqlite3"))
    mp.setenv("GIGSIM_CHAOS", "0")
    mp.setenv("MARKETPLACE_AUDIT_PATH", str(tmp / "audit.jsonl"))
    mp.setenv("MARKETPLACE_IDEM_DB", str(tmp / "idem.sqlite3"))
    mp.setenv("GIGSIM_TOKEN", AI_TOKEN)
    import gigsim.app as gs
    gs = importlib.reload(gs)
    import mcp_servers.marketplace.server as srv
    srv = importlib.reload(srv)
    srv.ADAPTER_OVERRIDES["transport"] = httpx.ASGITransport(app=gs.app)
    srv._adapters.clear()
    yield gs, srv
    mp.undo()
    importlib.reload(gs)                       # later test modules get the default policy back


class McpTools:
    """Stands in for the agent's Toolbox: every call goes through the real Marketplace MCP server."""

    def __init__(self, client: Client):
        self.client = client

    async def call(self, _node: str, tool: str, args: dict) -> dict:
        return (await self.client.call_tool(tool, args)).structured_content


async def test_fee_is_reported_and_priced_in(env):
    gs, srv = env
    assert gs.POLICY["version"] == "2026-11-02"
    with open("evals/datasets/dev_ai_ml.jsonl") as f:
        job = next(x["job"] for x in map(json.loads, f) if x["gold_action"] == "bid")
    from agent.nodes import base
    from agent.nodes.pricing import pricing
    async with Client(srv.mcp) as c:
        bal = (await c.call_tool("get_connects_balance", {})).structured_content
        assert bal["submission_fee"] == 4
        old = base._toolbox
        base.set_toolbox(McpTools(c))
        try:
            out = await pricing({"job": job, "user": {"hourly_rate": 40, "min_fixed_budget": 300},
                                 "fit_report": {"score": 0.9}, "steps": 0})
        finally:
            base._toolbox = old
    q = out["price_quote"]
    assert q["connects_cost"] == job["connects_cost"] + 4
    assert any("submission fee" in r for r in q["rationale"])
