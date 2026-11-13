"""GigSim adapter for the marketplace port.

Timeouts, bounded retries with exponential backoff and jitter (respecting
Retry-After) for 429, 5xx, timeouts and connection errors. Writes are retried
only when they carry an idempotency key, which GigSim honours, so a retry can
never submit twice. Errors become typed `MarketplaceError`s.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from agent.marketplace_port import DraftIn, JobQuery, MarketplaceError, marketplace_config
from mcp_servers.common import trace_id

KIND_BY_STATUS = {400: "invalid", 401: "unauthorized", 402: "insufficient_connects", 404: "not_found",
                  409: "conflict", 422: "invalid", 429: "rate_limited"}


class GigSimAdapter:
    mode = "gigsim"

    def __init__(self, token: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        cfg = marketplace_config()
        self.max_retries = int(cfg.get("max_retries", 3))
        self.client = httpx.AsyncClient(base_url=base_url, transport=transport,
                                        timeout=float(cfg.get("timeout_seconds", 10)),
                                        headers={"Authorization": f"Bearer {token}"})

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                    idempotency_key: str | None = None, retry: bool = True) -> dict:
        headers = {"X-Request-ID": trace_id()}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempts = 1 + (self.max_retries if retry else 0)
        last: MarketplaceError | None = None
        for attempt in range(attempts):
            try:
                r = await self.client.request(method, path, json=json, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = MarketplaceError("unavailable", f"GigSim not reachable: {type(e).__name__}", retryable=True)
            else:
                if r.status_code < 400:
                    return r.json()
                body = _body(r)
                if r.status_code == 429 or r.status_code >= 500:
                    ra = float(r.headers.get("Retry-After", 0) or 0)
                    kind = "rate_limited" if r.status_code == 429 else "unavailable"
                    last = MarketplaceError(kind, f"GigSim returned {r.status_code}", retryable=True,
                                            retry_after=ra or None, detail=body)
                elif r.status_code == 403 and isinstance(body.get("detail"), dict) and \
                        body["detail"].get("error") == "daily_submission_limit":
                    raise MarketplaceError("limit_reached", "daily submission limit reached", detail=body["detail"])
                else:
                    raise MarketplaceError(KIND_BY_STATUS.get(r.status_code, "invalid"),
                                           _message(body, r.status_code), detail=body.get("detail"))
            if attempt + 1 < attempts:
                delay = max(last.retry_after or 0, min(8.0, 0.5 * 2 ** attempt)) * (1 + random.random() * 0.25)
                await asyncio.sleep(delay)
        assert last is not None
        raise last

    # reads
    async def health(self) -> dict:
        return await self._call("GET", "/health")

    async def policy(self) -> dict:
        return await self._call("GET", "/v1/policy")

    async def search_jobs(self, query: JobQuery) -> dict:
        p = query.model_dump()
        p["skills"] = ",".join(query.skills) if query.skills else None
        return await self._call("GET", "/v1/jobs", params=p)

    async def get_job(self, job_id: str) -> dict:
        return await self._call("GET", f"/v1/jobs/{job_id}")

    async def get_client_history(self, client_id: str) -> dict:
        return await self._call("GET", f"/v1/clients/{client_id}")

    async def get_connects_balance(self) -> dict:
        return await self._call("GET", "/v1/account")

    async def get_market_stats(self, category: str | None) -> dict:
        return await self._call("GET", "/v1/market/stats", params={"category": category})

    async def list_proposals(self, status: str | None) -> dict:
        return await self._call("GET", "/v1/proposals", params={"status": status})

    async def get_proposal(self, proposal_id: str) -> dict:
        return await self._call("GET", f"/v1/proposals/{proposal_id}")

    async def list_messages(self, proposal_id: str) -> dict:
        return await self._call("GET", f"/v1/proposals/{proposal_id}/messages")

    async def get_events(self, since: int) -> dict:
        return await self._call("GET", "/v1/events", params={"since": since})

    async def preview_proposal(self, draft: DraftIn) -> dict:
        return await self._call("POST", "/v1/proposals/preview", json=draft.model_dump())

    # writes (retried only with an idempotency key)
    async def create_proposal_draft(self, draft: DraftIn, idempotency_key: str) -> dict:
        return await self._call("POST", "/v1/proposals", json=draft.model_dump(), idempotency_key=idempotency_key)

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict:
        return await self._call("POST", f"/v1/proposals/{proposal_id}/submit", idempotency_key=idempotency_key)

    async def send_message(self, proposal_id: str, text: str, idempotency_key: str) -> dict:
        return await self._call("POST", f"/v1/proposals/{proposal_id}/messages", json={"text": text},
                                idempotency_key=idempotency_key)


def _body(r: httpx.Response) -> dict:
    try:
        b = r.json()
        return b if isinstance(b, dict) else {"detail": b}
    except ValueError:
        return {"detail": r.text[:300]}


def _message(body: dict, code: int) -> str:
    d = body.get("detail", body)
    if isinstance(d, dict):
        return d.get("error", str(d))[:300] + (f": {d.get('problems')}" if d.get("problems") else "")
    return str(d)[:300] or f"HTTP {code}"
