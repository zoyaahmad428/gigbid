"""The single marketplace interface (requirement R1).

Everything that talks to a marketplace (the Marketplace MCP server, and through
it every agent) uses `MarketplacePort`. Two adapters implement it:

    mcp_servers/marketplace/adapters/gigsim.py       GigSim REST API (development and grading)
    mcp_servers/marketplace/adapters/upwork_live.py  official Upwork MCP server (live mode)

`make_adapter()` picks one from agent/config.yaml (`marketplace.mode`) or the
MARKETPLACE_MODE environment variable. Nothing above this file knows which
marketplace is in use.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, Field

ErrorKind = Literal["unavailable", "rate_limited", "limit_reached", "insufficient_connects", "not_found",
                    "conflict", "invalid", "unauthorized", "not_configured"]


class MarketplaceError(Exception):
    """A marketplace failure the caller can act on. `retryable` errors may succeed later."""

    def __init__(self, kind: ErrorKind, message: str, *, retryable: bool = False,
                 retry_after: float | None = None, detail: Any = None):
        super().__init__(message)
        self.kind, self.message, self.retryable, self.retry_after, self.detail = (
            kind, message, retryable, retry_after, detail)

    def __str__(self) -> str:
        return f"[{self.kind}{', retryable' if self.retryable else ''}] {self.message}"


class JobQuery(BaseModel):
    q: str | None = None
    category: str | None = None
    skills: list[str] | None = None
    budget_type: Literal["fixed", "hourly"] | None = None
    min_budget: float | None = None
    posted_after: str | None = None
    status: Literal["open", "historical"] = "open"
    limit: int = 20
    cursor: int | None = None


class DraftIn(BaseModel):
    job_id: str
    cover_letter: str
    bid_amount: float
    bid_type: Literal["fixed", "hourly"]
    screening_answers: list[str] = Field(default_factory=list)


class MarketplacePort(Protocol):
    mode: str

    async def health(self) -> dict[str, Any]: ...
    async def policy(self) -> dict[str, Any]: ...
    async def search_jobs(self, query: JobQuery) -> dict[str, Any]: ...
    async def get_job(self, job_id: str) -> dict[str, Any]: ...
    async def get_client_history(self, client_id: str) -> dict[str, Any]: ...
    async def get_connects_balance(self) -> dict[str, Any]: ...
    async def get_market_stats(self, category: str | None) -> dict[str, Any]: ...
    async def list_proposals(self, status: str | None) -> dict[str, Any]: ...
    async def get_proposal(self, proposal_id: str) -> dict[str, Any]: ...
    async def list_messages(self, proposal_id: str) -> dict[str, Any]: ...
    async def get_events(self, since: int) -> dict[str, Any]: ...
    async def preview_proposal(self, draft: DraftIn) -> dict[str, Any]: ...
    async def create_proposal_draft(self, draft: DraftIn, idempotency_key: str) -> dict[str, Any]: ...
    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]: ...
    async def send_message(self, proposal_id: str, text: str, idempotency_key: str) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...


CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def marketplace_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    m = dict(cfg.get("marketplace", {}))
    m["mode"] = os.environ.get("MARKETPLACE_MODE", m.get("mode", "gigsim"))
    return m


def make_adapter(credential: str | None, **overrides: Any) -> MarketplacePort:
    """Build the adapter for one user. `credential` is the user's marketplace token
    (a GigSim bearer token, or the user's id in the encrypted Upwork token store)."""
    cfg = {**marketplace_config(), **overrides}
    if cfg["mode"] == "gigsim":
        from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
        return GigSimAdapter(token=credential or "", base_url=os.environ.get("GIGSIM_URL", cfg.get(
            "gigsim_url", "http://localhost:8100")), transport=cfg.get("transport"))
    if cfg["mode"] == "upwork_live":
        from mcp_servers.marketplace.adapters.upwork_live import UpworkLiveAdapter
        return UpworkLiveAdapter(user_id=credential or "")
    raise ValueError(f"unknown marketplace mode {cfg['mode']!r}")
