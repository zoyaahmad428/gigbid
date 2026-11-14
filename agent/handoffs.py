"""Structured hand-offs between agents (assignment C3).

Anything a later step decides on travels as one of these Pydantic objects, never
as free text. LLM nodes are asked for JSON matching these schemas and the output
is validated; a node whose output fails validation is retried, then marked failed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Action = Literal["bid", "watch", "skip"]
RiskFlag = Literal["scam_pattern", "free_work_request", "off_platform_contact", "off_platform_payment",
                   "injected_instructions", "unverified_payment", "new_client_no_history", "low_client_rating",
                   "budget_unrealistic", "credential_request", "duplicate_post"]


class JobSpec(BaseModel):
    """Job Analyst output: what the client actually needs."""
    job_id: str
    title: str
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list, max_length=8,
                                    description="Concrete requirements, one per line; drive retrieval and the evidence matrix")
    budget_type: Literal["fixed", "hourly"] | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    timeline: str | None = None
    screening_questions: list[str] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list, description="Needs implied but not stated")
    category: str | None = None


class RiskReport(BaseModel):
    """Risk Assessor output. score: 0 = safe, 1 = certainly a scam / not worth the risk."""
    score: float = Field(ge=0, le=1)
    flags: list[RiskFlag | str] = Field(default_factory=list)
    explanation: str
    client_quality: float | None = Field(None, ge=0, le=1, description="From the client's marketplace history")
    injection_detected: bool = False
    injection_excerpt: str | None = Field(None, max_length=300, description="Quoted, never followed")
    is_duplicate_of: str | None = None


class EvidenceRef(BaseModel):
    evidence_id: str
    type: str
    title: str
    snippet: str = ""
    score: float | None = None


class RequirementMatch(BaseModel):
    """One row of the requirement-to-evidence matrix shown in the UI."""
    requirement: str
    status: Literal["proven", "partial", "missing"]
    evidence: list[EvidenceRef] = Field(default_factory=list)
    note: str = ""


class FitReport(BaseModel):
    """Fit Matcher output."""
    score: float = Field(ge=0, le=1)
    matrix: list[RequirementMatch] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    refused: bool = Field(False, description="Corrective retrieval found nothing to support a claim")


class BidDecision(BaseModel):
    action: Action
    reasons: list[str] = Field(min_length=1)
    fit_score: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)
    auto: bool = Field(False, description="True if taken without the user (only ever for skip)")

    @model_validator(mode="after")
    def _never_auto_bid(self):
        if self.auto and self.action != "skip":
            raise ValueError("only skips may be automatic; bids and watches need the user")
        return self


class PriceQuote(BaseModel):
    bid_type: Literal["fixed", "hourly"]
    amount: float = Field(gt=0)
    market_band: tuple[float, float] | None = None
    floor: float = Field(ge=0, description="The user's minimum")
    connects_cost: int = Field(ge=0)
    connects_balance: int | None = None
    worth_connects: bool
    rationale: list[str] = Field(min_length=1)


class Citation(BaseModel):
    sentence: str
    evidence_ids: list[str] = Field(default_factory=list)


class ProposalDraft(BaseModel):
    job_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    bid: PriceQuote | None = None
    revision: int = 0
    status: Literal["drafted", "verified", "critiqued", "compliant", "blocked", "pending_review",
                    "approved", "rejected", "submitted"] = "drafted"


class ClaimCheck(BaseModel):
    sentence: str
    label: Literal["supported", "partly_supported", "unsupported", "not_a_claim"]
    evidence_id: str | None = None
    score: float | None = None
    reasons: list[str] | None = None


class Critique(BaseModel):
    verdict: Literal["pass", "revise"]
    issues: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict, description="grounding, personalisation, length, clarity: 0-1")
    revision: int = 0


class ComplianceVerdict(BaseModel):
    allowed: bool
    violations: list[str] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list, description="What was removed (contact details, guarantees...)")
    cleaned_text: str | None = None
    reason: str = ""


class ApprovalDecision(BaseModel):
    """What the human sends back to resume an interrupted graph."""
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = None
    edited_bid: float | None = None
    reason: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _check(self):
        if self.action == "edit" and not (self.edited_text or self.edited_bid):
            raise ValueError("an edit needs edited_text or edited_bid")
        if self.action == "reject" and not self.reason:
            raise ValueError("a rejection needs a reason")
        return self
