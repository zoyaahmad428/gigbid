"""Output schemas for the GigSim dataset.

Every record carries provenance: `source` (real|synthetic), `source_dataset`
(Kaggle slug or "generator") and, for synthetic records, the generator name.
A record built from real text with synthetic edits (a planted scam line, an
injected instruction, a paraphrased duplicate) is `synthetic` and points to the
real record it came from through `derived_from`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["real", "synthetic"]
PersonaId = Literal["ai_ml", "fullstack", "data_analyst"]


class Provenance(BaseModel):
    source: Source
    source_dataset: str                       # Kaggle slug, or "generator"
    generator: str | None = None              # e.g. "synth.scams@1.0.0"
    derived_from: str | None = None           # id of the real record it was built from
    # For a real record that had some fields filled in by a generator (e.g. a real
    # posting with synthetic screening questions): which fields are synthetic.
    synthetic_fields: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    type: Literal["fixed", "hourly"]
    min: float | None = None                  # USD
    max: float | None = None
    currency: Literal["USD"] = "USD"


class Job(BaseModel):
    job_id: str
    title: str
    description: str
    skills: list[str] = Field(default_factory=list)
    category: str                             # normalised category (see taxonomy.py)
    persona_fit: list[PersonaId] = Field(default_factory=list)
    budget: Budget
    duration: str | None = None
    experience_level: str | None = None
    screening_questions: list[str] = Field(default_factory=list)
    client_id: str
    client_country: str | None = None
    posted_at: str                            # ISO 8601 UTC
    connects_cost: int
    provenance: Provenance


class LiveJob(Job):
    """A job in the live stream. Labels are ground truth for evaluation only and
    are written to a separate labels file, never served to agents."""


class JobLabel(BaseModel):
    job_id: str
    is_duplicate: bool = False
    dup_of: str | None = None
    dup_kind: Literal["exact", "paraphrase"] | None = None
    is_scam: bool = False
    scam_pattern: str | None = None
    has_injection: bool = False
    injection_type: str | None = None
    injection_location: Literal["description", "screening_question", "title"] | None = None


class ClientPublic(BaseModel):
    """What a marketplace shows about a client."""
    client_id: str
    country: str | None
    payment_verified: bool
    total_spent_usd: float
    jobs_posted: int
    hires: int
    hire_rate: float
    rating: float | None                       # rating freelancers gave this client (1-5)
    feedback_count: int
    past_contracts: list[dict] = Field(default_factory=list)  # real contracts, synthetic linkage
    member_since: str
    industry: str
    provenance: Provenance


class ClientHidden(BaseModel):
    """Hidden preferences that drive the simulated client's reply/hire decision.
    Never exposed through any read tool."""
    client_id: str
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    budget_ceiling_usd: float
    dislikes_generic_openings: bool
    prefers_short_proposals: bool
    responsiveness: float = Field(ge=0, le=1)   # base probability of replying at all
    is_scammer: bool = False
    provenance: Provenance


class EvidenceItem(BaseModel):
    evidence_id: str
    persona: PersonaId
    type: Literal["cv", "case_study", "readme", "code", "review", "proposal"]
    project_id: str | None = None
    title: str
    path: str                                  # relative path of the file holding the text
    skills: list[str] = Field(default_factory=list)
    skills_implied: list[str] = Field(default_factory=list)  # present but never named in text
    date: str
    client_industry: str | None = None
    outcome: Literal["won", "replied", "ignored"] | None = None
    provenance: Provenance


class PastProposal(BaseModel):
    proposal_id: str
    persona: PersonaId
    job_id: str                                # a historical job id
    job_title: str
    cover_letter: str
    bid_usd: float
    bid_type: Literal["fixed", "hourly"]
    cited_projects: list[str]
    specificity: float = Field(ge=0, le=1)     # generator knob, kept for analysis
    generic_opening: bool
    outcome: Literal["won", "replied", "ignored"]
    submitted_at: str
    provenance: Provenance


class DevItem(BaseModel):
    """Gold-labelled dev-set job for one persona."""
    job: Job
    persona: PersonaId
    gold_action: Literal["bid", "skip"]
    skip_reason: str | None = None
    is_scam: bool
    has_injection: bool
    relevant_projects: list[str]               # project_ids, best first
    relevant_evidence: list[str]               # top <=5 evidence_ids, best first (Recall@5 can reach 1.0)
    evidence_grades: dict[str, int] = Field(default_factory=dict)  # graded relevance for nDCG: 2 top, 1 related
    fair_price_band: Budget
