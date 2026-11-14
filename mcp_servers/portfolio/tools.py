"""Portfolio tools: search evidence, read a case study, list skills. Read-only.

Each call is scoped to the calling user's own portfolio (tenant), so one user
can never search another user's evidence.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from rag import retriever as retriever_factory
from rag.index.bm25 import tokenize
from rag.ingest.corpus import Doc, load_persona

EvidenceType = Literal["cv", "case_study", "readme", "code", "review", "proposal"]


class Filters(BaseModel):
    types: list[EvidenceType] | None = Field(None, description="Only these evidence types")
    exclude_types: list[EvidenceType] | None = Field(None, description="Leave out these types, e.g. ['proposal']")
    project_id: str | None = Field(None, description="Only evidence about this project")
    outcomes: list[Literal["won", "replied", "ignored"]] | None = Field(
        None, description="For past proposals: only these outcomes")
    skills: list[str] | None = Field(None, description="Evidence that names or implies any of these skills")
    date_from: date | None = None
    date_to: date | None = None


class EvidenceHit(BaseModel):
    evidence_id: str
    type: EvidenceType
    title: str
    source: str = Field(description="Path of the document inside the portfolio")
    date: str | None
    outcome: str | None = Field(description="won / replied / ignored for past proposals, else null")
    project_id: str | None
    client_industry: str | None
    skills: list[str]
    score: float
    snippet: str = Field(description="The passage that best matches the requirement")


class SearchResult(BaseModel):
    requirement: str
    retriever: str
    hits: list[EvidenceHit]


class RelatedDoc(BaseModel):
    evidence_id: str
    type: EvidenceType
    title: str


class CaseStudy(BaseModel):
    evidence_id: str
    project_id: str | None
    title: str
    date: str | None
    client_industry: str | None
    skills: list[str]
    skills_implied: list[str] = Field(description="Skills the project used but the text does not name")
    markdown: str
    related: list[RelatedDoc] = Field(description="Other documents about the same project")


class EvidenceDoc(BaseModel):
    evidence_id: str
    type: EvidenceType
    title: str
    source: str
    date: str | None
    project_id: str | None
    client_industry: str | None
    outcome: str | None
    skills: list[str]
    skills_implied: list[str]
    text: str = Field(description="Full text of the document")


class SkillEntry(BaseModel):
    skill: str
    evidence_count: int
    projects: int
    named_in: int = Field(description="Documents that name the skill")
    implied_in: int = Field(description="Documents that show the skill without naming it")
    last_used: str | None


class SkillList(BaseModel):
    persona: str
    skills: list[SkillEntry]


class Store:
    def __init__(self, persona: str):
        self.persona = persona
        self.docs: list[Doc] = load_persona(persona)
        self.by_id = {d.evidence_id: d for d in self.docs}
        self.name = os.environ.get("PORTFOLIO_RETRIEVER") or _deployed_variant()
        self.retriever = retriever_factory.build(self.docs, self.name)

    def _allowed(self, f: Filters | None) -> list[int]:
        out = []
        for i, d in enumerate(self.docs):
            if f:
                if f.types and d.type not in f.types:
                    continue
                if f.exclude_types and d.type in f.exclude_types:
                    continue
                if f.project_id and d.project_id != f.project_id:
                    continue
                if f.outcomes and d.outcome not in f.outcomes:
                    continue
                if f.skills:
                    want = {s.lower() for s in f.skills}
                    if not want & {s.lower() for s in d.skills + d.skills_implied}:
                        continue
                if f.date_from and (not d.date or d.date < f.date_from.isoformat()):
                    continue
                if f.date_to and (not d.date or d.date > f.date_to.isoformat()):
                    continue
            out.append(i)
        return out

    @staticmethod
    def snippet(text: str, query: str, width: int = 320) -> str:
        q = set(tokenize(query))
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and not p.startswith("#")] or [text]
        # Most words in common with the requirement; on ties prefer prose over code blocks and
        # tables (a case study's first paragraph is often a code fence, which grades badly).
        best = max(paras, key=lambda p: (len(q & set(tokenize(p))), not p.lstrip().startswith(("```", "|"))))
        return best if len(best) <= width else best[:width].rsplit(" ", 1)[0] + "..."

    def search(self, requirement: str, k: int, filters: Filters | None) -> SearchResult:
        hits = self.retriever.search(requirement, k, self._allowed(filters))
        out = []
        for i, score in hits:
            d = self.docs[i]
            out.append(EvidenceHit(evidence_id=d.evidence_id, type=d.type, title=d.title, source=d.path, date=d.date,
                                   outcome=d.outcome, project_id=d.project_id, client_industry=d.client_industry,
                                   skills=sorted(set(d.skills)), score=round(score, 4),
                                   snippet=self.snippet(d.text, requirement)))
        return SearchResult(requirement=requirement, retriever=self.name, hits=out)

    def case_study(self, case_id: str) -> CaseStudy | None:
        d = self.by_id.get(case_id)
        if d is None:                                   # accept a project id too
            d = next((x for x in self.docs if x.project_id == case_id and x.type == "case_study"), None)
        if d is None or d.type != "case_study":
            return None
        related = [RelatedDoc(evidence_id=x.evidence_id, type=x.type, title=x.title) for x in self.docs
                   if x.project_id == d.project_id and x.evidence_id != d.evidence_id and x.type != "proposal"]
        return CaseStudy(evidence_id=d.evidence_id, project_id=d.project_id, title=d.title, date=d.date,
                         client_industry=d.client_industry, skills=d.skills, skills_implied=d.skills_implied,
                         markdown=d.text, related=related)

    def skills(self) -> SkillList:
        agg: dict[str, dict] = defaultdict(lambda: {"docs": 0, "named": 0, "implied": 0, "projects": set(), "last": None})
        for d in self.docs:
            if d.type == "proposal":                    # a pitch is not evidence of having the skill
                continue
            for s, kind in [(s, "named") for s in set(d.skills)] + [(s, "implied") for s in set(d.skills_implied)]:
                a = agg[s]
                a["docs"] += 1
                a[kind] += 1
                if d.project_id:
                    a["projects"].add(d.project_id)
                if d.date and (a["last"] is None or d.date > a["last"]):
                    a["last"] = d.date
        entries = [SkillEntry(skill=s, evidence_count=a["docs"], projects=len(a["projects"]), named_in=a["named"],
                              implied_in=a["implied"], last_used=a["last"]) for s, a in agg.items()]
        entries.sort(key=lambda e: (-e.projects, -e.evidence_count, e.skill))
        return SkillList(persona=self.persona, skills=entries)


def _deployed_variant() -> str:
    """rag.variant from agent/config.yaml: the variant the evaluation chose."""
    import yaml
    from rag.ingest.corpus import ROOT
    return (yaml.safe_load((ROOT / "agent/config.yaml").read_text()).get("rag") or {}).get("variant", "bm25")


@lru_cache(maxsize=8)
def store_for(persona: str) -> Store:
    return Store(persona)


Requirement = Annotated[str, Field(min_length=3, max_length=2000,
                                   description="One requirement from the job, e.g. 'RAG over PDFs with citations'")]
K = Annotated[int, Field(ge=1, le=25, description="How many evidence items to return")]
