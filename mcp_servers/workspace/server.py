"""Workspace MCP server: calendar slots, tentative interview holds and email drafts.

Backed by a documented local mock (SQLite), not Google Calendar/Gmail: GigBid
only ever needs free/busy, a tentative hold and a draft, and the mock keeps the
demo free of real accounts. The agent sees MCP tools only, so swapping in the
Google APIs later changes this file and nothing else.

    python -m mcp_servers.workspace.server                     # stdio
    python -m mcp_servers.workspace.server --http --port 8203  # Streamable HTTP, bearer auth

Each user's calendar has working hours and some seeded busy blocks; holds and
drafts are stored per user. Nothing here ever sends an email or an invite.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import random
import sqlite3
import threading
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import mcp.types as t
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from mcp_servers.common import ROOT, BearerAuth, User, current_user, parse_users

READ = t.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = t.ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DELETE = t.ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

LOCAL = User(user_id=os.environ.get("WORKSPACE_USER", "local"))
WORK_START, WORK_END = 9, 17                      # local working hours, Mon-Fri
DEFAULT_TZ = os.environ.get("WORKSPACE_TZ", "Asia/Karachi")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS holds (hold_id TEXT PRIMARY KEY, user TEXT, start TEXT, end TEXT, title TEXT,
            note TEXT, status TEXT, idem TEXT, created TEXT, UNIQUE (user, idem));
        CREATE TABLE IF NOT EXISTS drafts (draft_id TEXT PRIMARY KEY, user TEXT, to_label TEXT, subject TEXT,
            body TEXT, idem TEXT, created TEXT, UNIQUE (user, idem));""")
        self.lock = threading.Lock()


STORE = Store(Path(os.environ.get("WORKSPACE_DB", ROOT / "mcp_servers/workspace/state/workspace.sqlite3")))


def _busy_blocks(user: str, day: dt.date, tz: ZoneInfo) -> list[tuple[dt.datetime, dt.datetime]]:
    """Seeded, repeatable 'existing meetings' so free slots look like a real calendar."""
    r = random.Random(hashlib.sha256(f"{user}:{day.isoformat()}".encode()).hexdigest())
    out = []
    for _ in range(r.choice([0, 1, 1, 2, 3])):
        h = r.randint(WORK_START, WORK_END - 1)
        start = dt.datetime.combine(day, dt.time(h, r.choice([0, 30])), tz)
        out.append((start, start + dt.timedelta(minutes=r.choice([30, 60, 90]))))
    rows = STORE.db.execute("SELECT start, end FROM holds WHERE user=? AND status='tentative'", (user,)).fetchall()
    out += [(dt.datetime.fromisoformat(a["start"]), dt.datetime.fromisoformat(a["end"])) for a in rows]
    return out


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ToolError(f"unknown time zone {name!r}; use an IANA name like 'Europe/London'") from e


class Slot(BaseModel):
    start: str = Field(description="ISO 8601 with offset, in the freelancer's time zone")
    end: str
    start_client_tz: str | None = Field(None, description="Same start time in the client's time zone")


class Slots(BaseModel):
    timezone: str
    client_timezone: str | None
    slots: list[Slot]


class Hold(BaseModel):
    hold_id: str
    start: str
    end: str
    title: str
    status: Literal["tentative", "cancelled"]
    idempotent_replay: bool = False


class Holds(BaseModel):
    holds: list[Hold]


class Draft(BaseModel):
    draft_id: str
    to_label: str
    subject: str
    body: str
    sent: bool = Field(False, description="Always false: this server only creates drafts")
    idempotent_replay: bool = False


Key = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]

mcp = MCPServer("gigbid-workspace", title="GigBid Workspace", version="1.0.0",
                instructions=("The freelancer's calendar and email drafts (local mock). Find free interview "
                              "slots, place tentative holds, and save email drafts. Nothing is ever sent."))


@mcp.tool(title="Find free interview slots", annotations=READ)
def get_free_slots(
        days: Annotated[int, Field(ge=1, le=14, description="How many days ahead to look, starting tomorrow")] = 5,
        duration_minutes: Annotated[int, Field(ge=15, le=120)] = 30,
        max_slots: Annotated[int, Field(ge=1, le=20)] = 6,
        timezone: Annotated[str, Field(description="Freelancer's IANA time zone")] = DEFAULT_TZ,
        client_timezone: Annotated[str | None, Field(description="Client's IANA time zone, to show both")] = None,
        start_date: Annotated[str | None, Field(description="YYYY-MM-DD; default is tomorrow")] = None) -> Slots:
    """Free slots in working hours (Mon-Fri 09:00-17:00 local), spread across days, not overlapping meetings
    or tentative holds."""
    user = current_user(LOCAL).user_id
    tz = _tz(timezone)
    ctz = _tz(client_timezone) if client_timezone else None
    day = dt.date.fromisoformat(start_date) if start_date else dt.datetime.now(tz).date() + dt.timedelta(days=1)
    step, dur = dt.timedelta(minutes=30), dt.timedelta(minutes=duration_minutes)
    per_day = max(1, max_slots // max(1, days) + 1)
    slots: list[Slot] = []
    for i in range(days):
        d = day + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        busy = _busy_blocks(user, d, tz)
        cur = dt.datetime.combine(d, dt.time(WORK_START), tz)
        end_of_day = dt.datetime.combine(d, dt.time(WORK_END), tz)
        taken = 0
        while cur + dur <= end_of_day and taken < per_day and len(slots) < max_slots:
            if all(cur + dur <= b0 or cur >= b1 for b0, b1 in busy):
                slots.append(Slot(start=cur.isoformat(), end=(cur + dur).isoformat(),
                                  start_client_tz=cur.astimezone(ctz).isoformat() if ctz else None))
                taken += 1
                cur += dt.timedelta(hours=2)                  # spread the offers out
            else:
                cur += step
    return Slots(timezone=timezone, client_timezone=client_timezone, slots=slots)


@mcp.tool(title="Place a tentative interview hold", annotations=WRITE)
def create_tentative_hold(start: Annotated[str, Field(description="ISO 8601 with offset")],
                          duration_minutes: Annotated[int, Field(ge=15, le=120)],
                          title: Annotated[str, Field(min_length=3, max_length=200)],
                          idempotency_key: Key,
                          note: Annotated[str, Field(max_length=1000)] = "") -> Hold:
    """Block time on the freelancer's own calendar as tentative. No invite is sent to anyone."""
    user = current_user(LOCAL).user_id
    try:
        s = dt.datetime.fromisoformat(start)
    except ValueError as e:
        raise ToolError("start must be ISO 8601, e.g. 2026-11-20T15:00:00+05:00") from e
    if s.tzinfo is None:
        raise ToolError("start needs a time zone offset")
    e = s + dt.timedelta(minutes=duration_minutes)
    with STORE.lock:
        row = STORE.db.execute("SELECT * FROM holds WHERE user=? AND idem=?", (user, idempotency_key)).fetchone()
        if row:
            return Hold(**{k: row[k] for k in ("hold_id", "start", "end", "title", "status")}, idempotent_replay=True)
        hid = "hold_" + hashlib.sha256(f"{user}:{idempotency_key}".encode()).hexdigest()[:12]
        STORE.db.execute("INSERT INTO holds VALUES (?,?,?,?,?,?,?,?,?)",
                         (hid, user, s.isoformat(), e.isoformat(), title, note, "tentative", idempotency_key,
                          dt.datetime.now(dt.timezone.utc).isoformat()))
    return Hold(hold_id=hid, start=s.isoformat(), end=e.isoformat(), title=title, status="tentative")


@mcp.tool(title="List tentative holds", annotations=READ)
def list_holds() -> Holds:
    """The freelancer's tentative interview holds."""
    user = current_user(LOCAL).user_id
    rows = STORE.db.execute("SELECT * FROM holds WHERE user=? ORDER BY start", (user,)).fetchall()
    return Holds(holds=[Hold(**{k: r[k] for k in ("hold_id", "start", "end", "title", "status")}) for r in rows])


@mcp.tool(title="Cancel a tentative hold", annotations=DELETE)
def cancel_hold(hold_id: str) -> Hold:
    """Cancel one of the freelancer's tentative holds."""
    user = current_user(LOCAL).user_id
    with STORE.lock:
        n = STORE.db.execute("UPDATE holds SET status='cancelled' WHERE user=? AND hold_id=?", (user, hold_id)).rowcount
        if not n:
            raise ToolError(f"no hold {hold_id}")
        r = STORE.db.execute("SELECT * FROM holds WHERE hold_id=?", (hold_id,)).fetchone()
    return Hold(**{k: r[k] for k in ("hold_id", "start", "end", "title", "status")})


@mcp.tool(title="Save an email draft", annotations=WRITE)
def create_email_draft(to_label: Annotated[str, Field(min_length=1, max_length=200,
                                                       description="Who it is for, e.g. 'Client: Acme' (no address)")],
                       subject: Annotated[str, Field(min_length=1, max_length=300)],
                       body: Annotated[str, Field(min_length=1, max_length=10000)],
                       idempotency_key: Key) -> Draft:
    """Save an email draft for the freelancer to review and send themselves. Never sends."""
    user = current_user(LOCAL).user_id
    with STORE.lock:
        row = STORE.db.execute("SELECT * FROM drafts WHERE user=? AND idem=?", (user, idempotency_key)).fetchone()
        if row:
            return Draft(draft_id=row["draft_id"], to_label=row["to_label"], subject=row["subject"],
                         body=row["body"], idempotent_replay=True)
        did = "draft_" + hashlib.sha256(f"{user}:{idempotency_key}".encode()).hexdigest()[:12]
        STORE.db.execute("INSERT INTO drafts VALUES (?,?,?,?,?,?,?)",
                         (did, user, to_label, subject, body, idempotency_key, dt.datetime.now(dt.timezone.utc).isoformat()))
    return Draft(draft_id=did, to_label=to_label, subject=subject, body=body)


def http_app():
    users = parse_users(os.environ.get("WORKSPACE_USERS", "dev-ws-ai=dev_ai"))
    return BearerAuth(mcp.streamable_http_app(), users)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8203)
    a = ap.parse_args()
    if a.http:
        import uvicorn
        uvicorn.run(http_app(), host=a.host, port=a.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
