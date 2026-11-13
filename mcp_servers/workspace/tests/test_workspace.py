"""Workspace MCP server tests through an in-memory MCP client."""
from __future__ import annotations

import datetime as dt
import importlib

import pytest
from mcp import Client


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DB", str(tmp_path / "ws.sqlite3"))
    import mcp_servers.workspace.server as s
    return importlib.reload(s)


async def test_free_slots_are_working_hours_and_weekdays(srv):
    async with Client(srv.mcp) as c:
        r = (await c.call_tool("get_free_slots", {"days": 7, "start_date": "2026-11-16", "timezone": "Asia/Karachi",
                                                  "client_timezone": "America/New_York"})).structured_content
    assert r["slots"]
    for s in r["slots"]:
        st = dt.datetime.fromisoformat(s["start"])
        assert st.weekday() < 5 and 9 <= st.hour < 17 and s["start_client_tz"].endswith("-05:00")


async def test_hold_is_idempotent_and_blocks_the_slot(srv):
    async with Client(srv.mcp) as c:
        first = (await c.call_tool("get_free_slots", {"days": 1, "start_date": "2026-11-16", "max_slots": 1})
                 ).structured_content["slots"][0]
        args = {"start": first["start"], "duration_minutes": 30, "title": "Interview: Acme", "idempotency_key": "hold-0001"}
        h1 = (await c.call_tool("create_tentative_hold", args)).structured_content
        h2 = (await c.call_tool("create_tentative_hold", args)).structured_content
        assert h1["hold_id"] == h2["hold_id"] and h2["idempotent_replay"]
        again = (await c.call_tool("get_free_slots", {"days": 1, "start_date": "2026-11-16", "max_slots": 1})
                 ).structured_content["slots"]
        assert not again or again[0]["start"] != first["start"]
        assert len((await c.call_tool("list_holds", {})).structured_content["holds"]) == 1


async def test_email_draft_never_sends(srv):
    async with Client(srv.mcp) as c:
        d = (await c.call_tool("create_email_draft", {"to_label": "Client: Acme", "subject": "Interview times",
                                                      "body": "Here are three times.", "idempotency_key": "draft-0001"})
             ).structured_content
    assert d["sent"] is False


async def test_bad_timezone(srv):
    async with Client(srv.mcp) as c:
        r = await c.call_tool("get_free_slots", {"timezone": "Mars/Base"})
    assert r.is_error
