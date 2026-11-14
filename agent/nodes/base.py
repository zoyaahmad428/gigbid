"""Shared node wrapper: budgets, retries, failure states, tracing, usage.

Every graph node is declared with @node("name"). The wrapper
  - stops before running if the thread is over its step or token budget
    (status "needs_attention": visible in the UI, never silently dropped);
  - binds the node name so LLM/tool calls land in the execution trace under it;
  - retries a failing node once for transient errors;
  - maps "marketplace unavailable" to status "degraded" (queued, resumable) and
    any other repeated failure to "dead_letter" with the error;
  - adds one step and merges token/cost usage into state.
"""
from __future__ import annotations

import asyncio
import functools
import traceback
from typing import Awaitable, Callable

from langgraph.errors import GraphBubbleUp

from agent.llm import LLMError
from agent.mcp_client import MarketplaceUnavailable, Toolbox, ToolCallError
from agent.settings import config
from agent.trace import TRACES, bind_node

TERMINAL = {"skipped", "watching", "rejected", "blocked", "needs_attention", "dead_letter", "degraded", "submitted",
            "done"}
_toolbox: Toolbox | None = None


def tools() -> Toolbox:
    global _toolbox
    if _toolbox is None:
        _toolbox = Toolbox()
    return _toolbox


def set_toolbox(tb: Toolbox) -> None:
    global _toolbox
    _toolbox = tb


def usage_entry(node: str, u: dict) -> dict:
    return {node: {k: u.get(k, 0) for k in ("tokens_in", "tokens_out", "cost_usd", "calls")},
            "total": {k: u.get(k, 0) for k in ("tokens_in", "tokens_out", "cost_usd", "calls")}}


def node(name: str, *, budget_key: str = "max_tokens_per_job"):
    def deco(fn: Callable[[dict], Awaitable[dict]]):
        @functools.wraps(fn)
        async def run(state: dict) -> dict:
            b = config()["budgets"]
            if state.get("status") in TERMINAL:
                return {}
            tokens = sum(state.get("usage", {}).get("total", {}).get(k, 0) for k in ("tokens_in", "tokens_out"))
            if state.get("steps", 0) >= b["max_graph_steps"]:
                return {"status": "needs_attention", "status_reason": f"step budget ({b['max_graph_steps']}) reached"}
            if tokens >= b[budget_key]:
                return {"status": "needs_attention", "status_reason": f"token budget ({b[budget_key]}) reached"}
            tok = bind_node(name)
            try:
                for attempt in range(2):
                    try:
                        out = await fn(state)
                        out = dict(out or {})
                        out["steps"] = out.get("steps", 0) + 1
                        return out
                    except GraphBubbleUp:
                        raise                     # interrupt() and Command(goto) travel as exceptions
                    except MarketplaceUnavailable as e:
                        TRACES.add("error", name, output={"degraded": e.message[:300]}, ok=False)
                        return {"status": "degraded", "steps": 1,
                                "status_reason": "Marketplace unavailable; the job is queued and will resume.",
                                "errors": [f"{name}: {e.message[:300]}"]}
                    except (LLMError, ToolCallError, asyncio.TimeoutError) as e:
                        if attempt == 0 and not (isinstance(e, ToolCallError) and not e.retryable):
                            continue
                        return _dead(name, e)
                    except Exception as e:  # noqa: BLE001
                        return _dead(name, e)
            finally:
                from agent.trace import _node
                _node.reset(tok)
            return {}
        run.node_name = name  # type: ignore[attr-defined]
        return run
    return deco


def _dead(name: str, e: Exception) -> dict:
    TRACES.add("error", name, output={"error": f"{type(e).__name__}: {e}"[:500],
                                      "trace": traceback.format_exc()[-1500:]}, ok=False)
    return {"status": "dead_letter", "status_reason": f"{name} failed: {type(e).__name__}: {str(e)[:200]}",
            "errors": [f"{name}: {type(e).__name__}: {str(e)[:300]}"], "steps": 1}


async def to_thread(fn, *a, **kw):
    """Run a blocking call (LLM SDK, cross-encoder) without blocking parallel branches.
    Context variables (user, thread, node) are copied into the worker thread."""
    return await asyncio.to_thread(fn, *a, **kw)
