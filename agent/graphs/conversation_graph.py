"""Conversation graph: one thread per client conversation turn (assignment C1).

read -> intent -> [slots if interview request] -> draft reply -> compliance -> approval (interrupt)
     -> send (+ tentative hold for an interview) -> END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.nodes.base import TERMINAL
from agent.nodes.client_liaison import approval, classify, draft_reply, read, reply_compliance, send, slots
from agent.state import ConversationState


def _stop_or(nxt):
    return lambda s: END if s.get("status") in TERMINAL | {"rejected", "blocked"} else nxt


def build_conversation_graph(checkpointer=None, store=None):
    g = StateGraph(ConversationState)
    for name, fn in [("read", read), ("intent", classify), ("slots", slots), ("draft_reply", draft_reply),
                     ("compliance", reply_compliance), ("approval", approval), ("send", send)]:
        g.add_node(name, fn)
    g.add_edge(START, "read")
    g.add_conditional_edges("read", _stop_or("intent"), ["intent", END])
    g.add_conditional_edges("intent", lambda s: END if s.get("status") in TERMINAL else (
        "slots" if s["intent"]["intent"] == "interview_request" else "draft_reply"), ["slots", "draft_reply", END])
    g.add_conditional_edges("slots", _stop_or("draft_reply"), ["draft_reply", END])
    g.add_conditional_edges("draft_reply", _stop_or("compliance"), ["compliance", END])
    g.add_conditional_edges("compliance", _stop_or("approval"), ["approval", END])
    g.add_conditional_edges("approval", _stop_or("send"), ["send", END])
    g.add_edge("send", END)
    return g.compile(checkpointer=checkpointer, store=store)
