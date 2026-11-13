"""Live Upwork adapter (live mode): NOT IMPLEMENTED YET.

Plan (assignment part A4):
- Connect to the official Upwork MCP server (https://mcp.upwork.com/mcp) with
  langchain-mcp-adapters' MultiServerMCPClient over Streamable HTTP, using the
  user's OAuth 2.1 tokens from the encrypted per-user token store
  (apps/api/auth). No scraping, browser automation or session cookies.
- Map Upwork's MCP tools onto the methods of `MarketplacePort`, so the graph
  cannot tell which marketplace it is using.
- Proposals and messages are created only as Upwork drafts, which the user then
  confirms on Upwork. `submit_proposal` and `send_message` must never post
  directly.
- Read tools run on a conservative schedule (see docs/RUNBOOK.md).

Until then every call raises `not_configured`, so selecting live mode fails
loudly instead of silently falling back to GigSim.
"""
from __future__ import annotations

from agent.marketplace_port import MarketplaceError


class UpworkLiveAdapter:
    mode = "upwork_live"

    def __init__(self, user_id: str):
        self.user_id = user_id

    def __getattr__(self, name: str):
        async def not_configured(*_args, **_kwargs):
            raise MarketplaceError("not_configured",
                                   "Live Upwork mode is not set up yet: connect an Upwork account first.")
        return not_configured

    async def aclose(self) -> None:
        return None
