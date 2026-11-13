"""Shared plumbing for GigBid's MCP servers.

- Bearer-token auth for the HTTP transport (ASGI middleware). The token decides
  the calling user; tools read it from `current_user()`, so every query is
  scoped to that user's tenant. Over stdio (Claude Desktop, Cursor) there is one
  local user, taken from an environment variable.
- One trace id per request (`X-Trace-Id` or W3C `traceparent`), passed down to
  GigSim and written to the audit log, so a trace links UI -> API -> graph -> tool.
- A per-user async token bucket.
- PII masking for anything written to logs.
- An append-only, hash-chained JSONL audit log (each line stores the hash of
  the previous line, so edits or deletions are detectable).
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class User:
    user_id: str
    persona: str | None = None          # which portfolio this user owns
    marketplace_token: str | None = None


_user: contextvars.ContextVar[User | None] = contextvars.ContextVar("gigbid_user", default=None)
_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar("gigbid_trace", default=None)


def parse_users(spec: str) -> dict[str, User]:
    """`token=user_id:persona:marketplace_token,...` (persona and marketplace token optional)."""
    users: dict[str, User] = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        token, _, rest = item.partition("=")
        parts = rest.split(":")
        users[token] = User(user_id=parts[0], persona=(parts[1] or None) if len(parts) > 1 else None,
                            marketplace_token=(parts[2] or None) if len(parts) > 2 else None)
    return users


def local_user(prefix: str) -> User:
    """The single user of a stdio / in-process server, from environment variables."""
    return User(user_id=os.environ.get(f"{prefix}_USER", "local"),
                persona=os.environ.get(f"{prefix}_PERSONA"),
                marketplace_token=os.environ.get(f"{prefix}_MARKETPLACE_TOKEN"))


def current_user(default: User | None = None) -> User:
    u = _user.get() or default
    if u is None:
        raise PermissionError("no authenticated user")
    return u


def trace_id() -> str:
    t = _trace.get()
    if not t:
        t = uuid.uuid4().hex
        _trace.set(t)
    return t


def bind(user: User | None = None, trace: str | None = None) -> None:
    """Set user/trace for code paths without HTTP (tests, in-process clients)."""
    if user is not None:
        _user.set(user)
    if trace is not None:
        _trace.set(trace)


class BearerAuth:
    """ASGI middleware: 401 unless `Authorization: Bearer <token>` names a known user."""

    def __init__(self, app, users: dict[str, User], open_paths: tuple[str, ...] = ("/health",)):
        self.app, self.users, self.open_paths = app, users, open_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        tp = headers.get("traceparent", "")
        trace = headers.get("x-trace-id") or (tp.split("-")[1] if tp.count("-") >= 3 else None) or uuid.uuid4().hex
        if scope["path"] in self.open_paths:
            return await self._json(send, 200, {"ok": True})
        auth = headers.get("authorization", "")
        user = self.users.get(auth[7:]) if auth.lower().startswith("bearer ") else None
        if user is None:
            return await self._json(send, 401, {"error": "invalid or missing bearer token"},
                                    [(b"www-authenticate", b"Bearer")])
        t1, t2 = _user.set(user), _trace.set(trace)
        try:
            await self.app(scope, receive, send)
        finally:
            _user.reset(t1)
            _trace.reset(t2)

    @staticmethod
    async def _json(send, status: int, body: dict, extra: list | None = None):
        data = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), *(extra or [])]})
        await send({"type": "http.response.body", "body": data})


class RateLimiter:
    """Per-user token bucket. `acquire` waits (politely) instead of failing."""

    def __init__(self, per_minute: float, burst: int):
        self.rate, self.burst = per_minute / 60.0, burst
        self.state: dict[str, tuple[float, float]] = {}
        self.lock = asyncio.Lock()

    async def acquire(self, key: str) -> float:
        waited = 0.0
        while True:
            async with self.lock:
                now = time.monotonic()
                tokens, last = self.state.get(key, (float(self.burst), now))
                tokens = min(self.burst, tokens + (now - last) * self.rate)
                if tokens >= 1:
                    self.state[key] = (tokens - 1, now)
                    return waited
                self.state[key] = (tokens, now)
                wait = (1 - tokens) / self.rate
            await asyncio.sleep(wait)
            waited += wait


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_RANGE_RE = re.compile(r"\d{1,7}(?:,\d{3})*(?:\.\d+)?\s?[-–]\s?\d{1,7}(?:,\d{3})*(?:\.\d+)?")
_DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}")


def looks_like_phone(s: str) -> bool:
    """A phone-shaped match counts as a phone number only if it has 10-15 digits
    (or starts with '+' and has 8+), and is not a date or a numeric range such as
    "1000-2000" (an earlier version masked those as [PHONE])."""
    s = s.strip()
    digits = sum(c.isdigit() for c in s)
    if digits > 15 or (digits < 10 and not (s.startswith("+") and digits >= 8)):
        return False
    return not (_RANGE_RE.fullmatch(s) or _DATE_RE.fullmatch(s))


_SECRET_KEYS = {"token", "access_token", "refresh_token", "authorization", "password", "api_key", "secret"}


def mask(value: Any) -> Any:
    """Mask emails, phone and card numbers in strings; drop secret-looking keys."""
    if isinstance(value, str):
        v = _CARD.sub(lambda m: "[CARD]" if sum(c.isdigit() for c in m.group(0)) >= 13 and not
                      _DATE_RE.search(m.group(0)) else m.group(0), _EMAIL.sub("[EMAIL]", value))
        return _PHONE.sub(lambda m: "[PHONE]" if looks_like_phone(m.group(0)) else m.group(0), v)
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in _SECRET_KEYS else mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


class AuditLog:
    """Append-only JSONL, hash-chained. Never rewritten; verify() detects tampering."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._prev = self._last_hash()

    def _last_hash(self) -> str:
        last = None
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
        return json.loads(last)["hash"] if last else "0" * 64

    async def write(self, *, user: str, tool: str, args: dict, result: Any, ok: bool, ms: float) -> None:
        async with self._lock:
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "trace_id": trace_id(),
                   "user": user, "tool": tool, "args": mask(args), "ok": ok, "ms": round(ms, 1),
                   "result": mask(result), "prev": self._prev}
            rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._prev = rec["hash"]

    def verify(self) -> bool:
        prev = "0" * 64
        if not self.path.exists():
            return True
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                h = rec.pop("hash")
                if rec["prev"] != prev or hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest() != h:
                    return False
                prev = h
        return True
