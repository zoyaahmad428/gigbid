"""Long-term memory on a LangGraph store. Writes go only through the promotion rules.

Namespaces (per user = tenant):
  ("style_edits", user)            raw edit *signals* (not text) per approved edit; input to promotion
  ("style", user)                  promoted style rules  <- what the Writer sees
  ("style_blocked", user)          rules the user deleted (not re-promoted for 30 days)
  ("outcome_counts", user, cat)    per-category feature counts from resolved proposals
  ("outcome", user)                promoted lessons per category  <- what the Writer sees
  ("outcome_seen", user)           proposal ids already counted (idempotent outcome updates)
"""
from __future__ import annotations

import os
import time
from contextlib import ExitStack
from pathlib import Path

from langgraph.store.base import BaseStore

from agent.memory.promotion_rules import (describe_style, edit_signals, outcome_features, promote_outcomes,
                                          promote_style)
from agent.settings import ROOT, config, postgres_url

_stack = ExitStack()
_store: BaseStore | None = None
BLOCK_SECONDS = 30 * 86400


def get_store() -> BaseStore:
    """Postgres store when DATABASE_URL points at Postgres, else SQLite (local dev)."""
    global _store
    if _store is None:
        url = postgres_url()
        if url:
            from langgraph.store.postgres import PostgresStore
            _store = _stack.enter_context(PostgresStore.from_conn_string(url))
        else:
            from langgraph.store.sqlite import SqliteStore
            path = Path(os.environ.get("GIGBID_STORE_DB", ROOT / "data/processed/memory.sqlite3"))
            path.parent.mkdir(parents=True, exist_ok=True)
            _store = _stack.enter_context(SqliteStore.from_conn_string(str(path)))
        _store.setup()
    return _store


def set_store(store: BaseStore) -> None:
    global _store
    _store = store


# ------------------------------------------------------------------ style
def record_edit(user: str, before: str, after: str, *, thread_id: str) -> dict:
    """Store the edit's signals, re-run the promotion rule, return {signals, promoted}."""
    s = get_store()
    sigs = edit_signals(before, after)
    s.put(("style_edits", user), f"{int(time.time() * 1000)}_{thread_id}", {"signals": sigs, "thread_id": thread_id,
                                                                          "ts": time.time()})
    return {"signals": sigs, "promoted": refresh_style(user)}


def refresh_style(user: str) -> dict[str, int]:
    s = get_store()
    edits = sorted(s.search(("style_edits", user), limit=1000), key=lambda i: i.key)
    blocked = {i.key for i in s.search(("style_blocked", user), limit=1000)
               if time.time() - i.value["ts"] < BLOCK_SECONDS}
    promoted = promote_style([i.value["signals"] for i in edits], blocked, config()["memory"])
    current = {i.key for i in s.search(("style", user), limit=1000)}
    for rule, n in promoted.items():
        s.put(("style", user), rule, {"rule": rule, "text": describe_style(rule), "support": n, "ts": time.time()})
    for rule in current - set(promoted):                  # support fell out of the window
        s.delete(("style", user), rule)
    return promoted


def style_rules(user: str) -> list[dict]:
    return [i.value for i in get_store().search(("style", user), limit=100)]


def delete_style_rule(user: str, rule: str) -> None:
    s = get_store()
    s.delete(("style", user), rule)
    s.put(("style_blocked", user), rule, {"ts": time.time()})


# ------------------------------------------------------------------ outcomes
ENGAGED = {"replied", "interview", "hired"}
RESOLVED = ENGAGED | {"lost"}


def record_outcome(user: str, proposal: dict, category: str, market_p50: float | None) -> list[dict] | None:
    """Count one resolved proposal once, then re-derive the category's lessons."""
    if proposal.get("status") not in RESOLVED:
        return None
    s = get_store()
    if s.get(("outcome_seen", user), proposal["proposal_id"]):
        return None
    feats = outcome_features(proposal, market_p50)
    engaged = proposal["status"] in ENGAGED
    ns = ("outcome_counts", user, category)
    for feat, val in feats.items():
        item = s.get(ns, feat)
        counts = dict(item.value) if item else {}
        e, t = counts.get(val, [0, 0])
        counts[val] = [e + int(engaged), t + 1]
        s.put(ns, feat, counts)
    s.put(("outcome_seen", user), proposal["proposal_id"], {"ts": time.time(), "engaged": engaged})
    counts = {i.key: i.value for i in s.search(ns, limit=50)}
    lessons = promote_outcomes(counts, config()["memory"])
    s.put(("outcome", user), category, {"category": category, "lessons": lessons, "ts": time.time()})
    return lessons


def outcome_lessons(user: str, category: str) -> list[dict]:
    item = get_store().get(("outcome", user), category)
    return item.value["lessons"] if item else []


def delete_outcome_lessons(user: str, category: str) -> None:
    get_store().delete(("outcome", user), category)
