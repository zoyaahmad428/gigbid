"""MCP access for agents, with per-agent tool allowlists (assignment A6).

Agents never import a server, adapter, database or file: they call
`tools.call(agent, tool, args)`. The toolbox checks the agent's allowlist,
applies a timeout and bounded retries for transport failures, records the call
in the execution trace, and turns tool errors into typed exceptions so the graph
can enter degraded mode ("marketplace unavailable") instead of crashing.

Transport per server comes from config `mcp:`:
  inprocess  the server object in this process (local dev, tests, evaluation)
  http       Streamable HTTP URL with the user's bearer token (docker compose / deployment)

LangGraph's prebuilt agents (the R&D question C baselines) get the same tools as
LangChain tools through langchain-mcp-adapters; see `langchain_tools()`.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from mcp import Client

from agent.settings import config
from agent.trace import TRACES

ALLOWLIST: dict[str, set[str]] = {
    "scout": {"search_jobs", "get_job", "marketplace_status"},
    "analyst": set(),
    "risk_assessor": {"get_job", "get_client_history"},
    "fit_matcher": {"search_evidence", "get_evidence", "list_skills"},
    "pricing": {"get_market_stats", "get_connects_balance", "get_client_history"},
    "writer": {"search_evidence", "get_evidence"},
    "critic": {"search_evidence", "get_evidence"},
    "compliance": set(),
    "client_liaison": {"list_messages", "list_proposals", "get_job", "search_evidence", "get_evidence", "get_free_slots",
                       "create_tentative_hold", "create_email_draft", "send_message"},
    # Not an LLM: executes what the human approved (and only that).
    "executor": {"create_proposal_draft", "submit_proposal", "send_message", "get_connects_balance",
                 "create_tentative_hold", "list_proposals"},
    "monitor": {"marketplace_status", "get_events", "list_proposals"},
}

SERVER_OF = {
    **{t: "portfolio" for t in ("search_evidence", "get_case_study", "get_evidence", "list_skills")},
    **{t: "workspace" for t in ("get_free_slots", "create_tentative_hold", "list_holds", "cancel_hold",
                                "create_email_draft")},
}


class ToolCallError(RuntimeError):
    def __init__(self, tool: str, message: str, kind: str = "error", retryable: bool = False):
        super().__init__(f"{tool}: {message}")
        self.tool, self.message, self.kind, self.retryable = tool, message, kind, retryable


class MarketplaceUnavailable(ToolCallError):
    pass


class ToolNotAllowed(PermissionError):
    pass


def _server_objects() -> dict[str, Any]:
    from mcp_servers.marketplace.server import mcp as marketplace
    from mcp_servers.portfolio.server import mcp as portfolio
    from mcp_servers.workspace.server import mcp as workspace
    return {"portfolio": portfolio, "marketplace": marketplace, "workspace": workspace}


class Toolbox:
    def __init__(self, mode: str | None = None, urls: dict[str, str] | None = None,
                 tokens: dict[str, str] | None = None):
        mc = config().get("mcp", {})
        self.mode = mode or os.environ.get("MCP_MODE", mc.get("mode", "inprocess"))
        self.urls = urls or {k: os.environ.get(f"MCP_{k.upper()}_URL", v) for k, v in mc.get("urls", {}).items()}
        self.tokens = tokens or {}
        self.timeout = float(mc.get("timeout_seconds", 30))
        self.retries = int(mc.get("retries", 2))
        self._objs: dict[str, Any] | None = None

    def _target(self, server: str):
        if self.mode == "inprocess":
            if self._objs is None:
                self._objs = _server_objects()
            return self._objs[server]
        return self.urls[server]

    async def _call_once(self, server: str, tool: str, args: dict) -> Any:
        target = self._target(server)
        if isinstance(target, str):
            import httpx2
            from mcp.client.session import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            from mcp_servers.common import trace_id
            headers = {"X-Trace-Id": trace_id()}
            if self.tokens.get(server):
                headers["Authorization"] = f"Bearer {self.tokens[server]}"
            async with httpx2.AsyncClient(headers=headers, timeout=self.timeout) as h:
                async with streamable_http_client(target, http_client=h) as streams:
                    async with ClientSession(*streams[:2]) as s:
                        await s.initialize()
                        return await s.call_tool(tool, args)
        async with Client(target) as c:
            return await c.call_tool(tool, args)

    async def call(self, agent: str, tool: str, args: dict | None = None) -> dict:
        if tool not in ALLOWLIST.get(agent, set()):
            raise ToolNotAllowed(f"agent {agent!r} may not call {tool!r}")
        args = args or {}
        server = SERVER_OF.get(tool, "marketplace")
        last: Exception | None = None
        for attempt in range(1 + self.retries):
            start = time.perf_counter()
            try:
                res = await asyncio.wait_for(self._call_once(server, tool, args), self.timeout)
            except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                last = e
                TRACES.add("tool", tool, input=args, output={"error": type(e).__name__}, ok=False,
                           ms=(time.perf_counter() - start) * 1000)
                await asyncio.sleep(0.5 * 2 ** attempt)
                continue
            ms = (time.perf_counter() - start) * 1000
            if res.is_error:
                text = res.content[0].text if res.content else "unknown error"
                TRACES.add("tool", tool, input=args, output={"error": text[:500]}, ok=False, ms=ms)
                m = re.search(r"\[(\w+)(, retryable)?\]", text)
                kind = m.group(1) if m else "error"
                if kind in ("unavailable", "rate_limited") or "not reachable" in text:
                    raise MarketplaceUnavailable(tool, text, kind, True)
                raise ToolCallError(tool, text, kind, bool(m and m.group(2)))
            out = res.structured_content or {}
            TRACES.add("tool", tool, input=args, output=_summary(out), ms=ms)
            return out
        if server == "marketplace":
            raise MarketplaceUnavailable(tool, f"no response after {1 + self.retries} attempts: {last}", "unavailable", True)
        raise ToolCallError(tool, f"no response: {last}", "unavailable", True)


def _summary(out: dict) -> Any:
    if "hits" in out:
        return {"hits": [{"id": h["evidence_id"], "score": h.get("score")} for h in out["hits"]]}
    if "jobs" in out:
        return {"jobs": [j["job_id"] for j in out["jobs"]], "total": out.get("total")}
    return out


async def langchain_tools(agent: str, toolbox: Toolbox) -> list:
    """The agent's allowed tools as LangChain tools (for the prebuilt-agent baselines)."""
    from langchain_core.tools import StructuredTool

    tools = []
    servers = _server_objects()
    for name in sorted(ALLOWLIST[agent]):
        server = servers[SERVER_OF.get(name, "marketplace")]
        async with Client(server) as c:
            spec = next(t for t in (await c.list_tools()).tools if t.name == name)

        async def run(_n=name, **kwargs):
            return await toolbox.call(agent, _n, kwargs)

        tools.append(StructuredTool.from_function(coroutine=run, name=name, description=spec.description or name,
                                                  args_schema=spec.input_schema))
    return tools
