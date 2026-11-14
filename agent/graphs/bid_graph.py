"""Bid graph: one thread per job (assignment C1). Rendered by `python -m agent.graphs.render`.

load_job -> Send(analyst | risk_assessor | fit_matcher) -> decision
  skip/watch -> END
  bid -> pricing -> writer -> verifier -> critic --revise (max 2)--> writer
                                          \\-> compliance --blocked--> END
                                                         \\-> approval_gate (interrupt)
                                                              approve -> submit -> END
                                                              edit    -> verifier -> compliance -> approval_gate
                                                              reject  -> END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent.nodes.analyst import analyst
from agent.nodes.approval import approval_gate, route_after_gate, submit
from agent.nodes.base import TERMINAL, node, tools
from agent.nodes.compliance import compliance
from agent.nodes.critic import critic, route_after_critic
from agent.nodes.decision import decide
from agent.nodes.fit_matcher import fit_matcher
from agent.nodes.pricing import pricing
from agent.nodes.risk_assessor import risk_assessor
from agent.nodes.verifier import verifier
from agent.nodes.writer import writer
from agent.state import BidState

PARALLEL = ["analyst", "risk_assessor", "fit_matcher"]


@node("load_job")
async def load_job(state: dict) -> dict:
    """The Scout hands in a job id; load the job through the Marketplace server."""
    job = state.get("job") or await tools().call("scout", "get_job", {"job_id": state["job_id"]})
    return {"job": job, "status": "running", "status_reason": "analysing"}


def fan_out(state: dict):
    if state.get("status") in TERMINAL:
        return END
    return [Send(n, state) for n in PARALLEL]


def after_decision(state: dict) -> str:
    return "pricing" if state.get("decision", {}).get("action") == "bid" and state.get("status") not in TERMINAL else END


def stop_or(nxt: str):
    def route(state: dict) -> str:
        return END if state.get("status") in TERMINAL else nxt
    route.__name__ = f"to_{nxt}"
    return route


def after_verifier(state: dict) -> str:
    if state.get("status") in TERMINAL:
        return END
    return "compliance" if state.get("after_edit") else "critic"


def build_bid_graph(checkpointer=None, store=None):
    g = StateGraph(BidState)
    for name, fn in [("load_job", load_job), ("analyst", analyst), ("risk_assessor", risk_assessor),
                     ("fit_matcher", fit_matcher), ("decision", decide), ("pricing", pricing), ("writer", writer),
                     ("verifier", verifier), ("critic", critic), ("compliance", compliance),
                     ("approval_gate", approval_gate), ("submit", submit)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_job")
    g.add_conditional_edges("load_job", fan_out, PARALLEL + [END])
    g.add_edge(PARALLEL, "decision")                  # waits for all three branches
    g.add_conditional_edges("decision", after_decision, ["pricing", END])
    g.add_conditional_edges("pricing", stop_or("writer"), ["writer", END])
    g.add_conditional_edges("writer", stop_or("verifier"), ["verifier", END])
    g.add_conditional_edges("verifier", after_verifier, ["critic", "compliance", END])
    g.add_conditional_edges("critic", lambda s: {"writer": "writer", "compliance": "compliance"}.get(
        route_after_critic(s), END), ["writer", "compliance", END])
    g.add_conditional_edges("compliance", stop_or("approval_gate"), ["approval_gate", END])
    g.add_conditional_edges("approval_gate", lambda s: {"submit": "submit", "verifier": "verifier"}.get(
        route_after_gate(s), END), ["submit", "verifier", END])
    g.add_edge("submit", END)
    return g.compile(checkpointer=checkpointer, store=store)
