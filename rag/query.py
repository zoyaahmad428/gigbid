"""Turn a job post into retrieval queries.

`requirements()` is the rule-based decomposition used by V1: it keeps sentences
and bullet points that state a need (a skill name or a requirement verb), plus
one query listing the job's skills. The Job Analyst agent produces an LLM-made
JobSpec later; when one is available its requirements are passed in instead.
"""
from __future__ import annotations

import re

from rag.index.bm25 import tokenize

NEED_RE = re.compile(r"\b(need|needs|needed|must|require|required|requirement|experience|expert|build|develop|"
                     r"create|implement|integrate|design|analy[sz]e|dashboard|model|automate|migrate|deploy|"
                     r"looking for|should|familiar|knowledge|proficien|skills?)\b", re.I)
SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+|\s[•\-\*]\s|;\s")


def post_text(job: dict, max_chars: int = 2000) -> str:
    skills = f"\nSkills: {', '.join(job.get('skills', []))}" if job.get("skills") else ""
    return f"{job['title']}\n{job['description'][:max_chars]}{skills}"


def requirements(job: dict, max_n: int = 5) -> list[str]:
    parts = [p.strip(" -•*\t") for p in SPLIT_RE.split(job["description"]) if p and p.strip()]
    skills = [s.lower() for s in job.get("skills", [])]
    scored = []
    for i, p in enumerate(parts):
        if len(p) < 12 or len(p) > 400:
            continue
        low = p.lower()
        s = 2 * sum(sk in low for sk in skills) + (1 if NEED_RE.search(p) else 0)
        if s:
            scored.append((s, i, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    reqs: list[str] = []
    seen: list[set[str]] = []
    for _, _, p in scored:
        toks = set(tokenize(p))
        if any(len(toks & s) / max(1, len(toks | s)) > 0.6 for s in seen):   # near-duplicate requirement
            continue
        reqs.append(p)
        seen.append(toks)
        if len(reqs) >= max_n - 1:
            break
    if job.get("skills"):
        reqs.append(f"{job['title']}: experience with {', '.join(job['skills'][:8])}")
    return reqs or [post_text(job, 600)]
