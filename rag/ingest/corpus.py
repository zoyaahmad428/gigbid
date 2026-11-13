"""Load a persona's portfolio as a list of documents with metadata.

One `Doc` per evidence item in `evidence_index.jsonl`: CV, case studies, repo
READMEs, code files, reviews and past proposals. The text comes from the file
the item points to (or from the reviews/proposals JSONL line). The CV is read
from cv.md; cv.pdf has the same content and is what the PDF loader ingests
in the full RAG pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAS_DIR = ROOT / "data" / "personas"


@dataclass
class Doc:
    evidence_id: str
    persona: str
    type: str
    title: str
    text: str
    path: str
    project_id: str | None = None
    skills: list[str] = field(default_factory=list)
    skills_implied: list[str] = field(default_factory=list)
    date: str | None = None
    client_industry: str | None = None
    outcome: str | None = None

    def meta(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "text"}


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_persona(persona: str, base: Path = PERSONAS_DIR) -> list[Doc]:
    d = base / persona
    if not (d / "evidence_index.jsonl").exists():
        raise FileNotFoundError(f"no portfolio for persona {persona!r} in {base}")
    reviews = {r["review_id"]: r for r in _jsonl(d / "reviews.jsonl")}
    proposals = {p["proposal_id"]: p for p in _jsonl(d / "proposals.jsonl")}
    docs = []
    for e in _jsonl(d / "evidence_index.jsonl"):
        file_part, _, anchor = e["path"].partition("#")
        if e["type"] == "review":
            r = reviews[anchor]
            text = f"Client review ({r['rating']}/5, {r['reviewer']}): {r['text']}"
        elif e["type"] == "proposal":
            p = proposals[anchor]
            text = (f"Proposal for: {p['job_title']}\nOutcome: {p['outcome']}\nBid: ${p['bid_usd']:g} "
                    f"({p['bid_type']})\n\n{p['cover_letter']}")
        elif e["type"] == "cv":
            text = (d / "cv.md").read_text(encoding="utf-8")
        else:
            text = (d / file_part).read_text(encoding="utf-8")
        docs.append(Doc(evidence_id=e["evidence_id"], persona=persona, type=e["type"], title=e["title"],
                        text=text, path=e["path"], project_id=e.get("project_id"), skills=e.get("skills", []),
                        skills_implied=e.get("skills_implied", []), date=e.get("date"),
                        client_industry=e.get("client_industry"), outcome=e.get("outcome")))
    return docs


def available_personas(base: Path = PERSONAS_DIR) -> list[str]:
    return sorted(p.name for p in base.iterdir() if (p / "evidence_index.jsonl").exists())
