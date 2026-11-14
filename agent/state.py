"""Graph state (assignment C2).

Three things are kept apart:
  State (this file)          what one job or conversation needs right now; checkpointed per thread.
  Execution trace            every LLM/tool call with inputs, outputs, tokens, cost: agent/trace.py.
                             State keeps only counters, never raw tool output.
  Long-term memory           promoted knowledge only, in the LangGraph store: agent/memory/.

Reducers: Job Analyst, Risk Assessor and Fit Matcher run in parallel and write
different keys; the keys they share (usage, steps, errors) have reducers so
parallel writes add up instead of overwriting each other.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

ThreadStatus = Literal["running", "awaiting_approval", "submitted", "skipped", "watching", "rejected", "blocked",
                       "needs_attention", "dead_letter", "degraded", "replied", "done"]


def merge_usage(a: dict | None, b: dict | None) -> dict:
    out = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, (int, float)):
            out[k] = round(out.get(k, 0) + v, 8)
        elif isinstance(v, dict):
            out[k] = merge_usage(out.get(k), v)
    return out


def last(_a: Any, b: Any) -> Any:
    return b


class UserContext(TypedDict, total=False):
    """From the user's settings in the app (not from files): identity, rates, preferences."""
    user_id: str
    persona: str
    marketplace_token: str
    name: str
    headline: str
    hourly_rate: float
    min_fixed_budget: float
    autonomy_auto_skip: bool
    timezone: str


class BidState(TypedDict, total=False):
    # input
    thread_id: str
    user: UserContext
    job_id: str
    force_bid: bool                                   # user overrode a skip/watch recommendation
    after_edit: bool                                  # re-check path after a human edit (skips the Critic)
    job: dict                                         # the job as the marketplace returned it
    # parallel analyses (each written by one branch)
    job_spec: dict                                    # JobSpec
    risk_report: dict                                 # RiskReport
    fit_report: dict                                  # FitReport
    evidence: dict[str, dict]                         # evidence_id -> {title, type, text, skills...} for the writer
    # decision and pricing
    decision: dict                                    # BidDecision
    price_quote: dict                                 # PriceQuote
    # drafting loop
    draft: dict                                       # ProposalDraft (current)
    drafts: Annotated[list[dict], operator.add]       # every version, for the UI and the report
    verification: dict                                # {results, stats_before, stats_after}
    critiques: Annotated[list[dict], operator.add]    # Critique per round
    compliance: dict                                  # ComplianceVerdict
    revision: int
    # approval and outcome
    preview: dict                                     # dry-run preview from the marketplace
    approvals: Annotated[list[dict], operator.add]    # ApprovalDecision history (edits, rejects)
    submission: dict                                  # marketplace result
    outcome: dict
    # bookkeeping (reducers: parallel branches add up)
    status: Annotated[ThreadStatus, last]
    status_reason: Annotated[str, last]
    steps: Annotated[int, operator.add]
    usage: Annotated[dict, merge_usage]               # tokens and cost per node
    errors: Annotated[list[str], operator.add]


class ConversationState(TypedDict, total=False):
    thread_id: str
    user: UserContext
    proposal_id: str
    job: dict
    proposal: dict
    messages: list[dict]                              # as listed by the marketplace (untrusted text)
    intent: dict
    slots: list[dict]
    reply: dict                                       # {text, citations}
    compliance: dict
    approvals: Annotated[list[dict], operator.add]
    sent: dict
    hold: dict
    status: Annotated[ThreadStatus, last]
    status_reason: Annotated[str, last]
    steps: Annotated[int, operator.add]
    usage: Annotated[dict, merge_usage]
    errors: Annotated[list[str], operator.add]
