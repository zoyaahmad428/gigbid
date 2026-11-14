"""Fit Matcher: map every requirement to portfolio evidence, with a corrective step.

Runs in parallel with the Job Analyst, so it extracts requirements from the post
itself (rule-based, as in RAG variant V1). For each requirement it searches the
Portfolio MCP server, grades the hits with the cross-encoder, and marks the
requirement proven / partial / missing. If nothing is even partially proven it
re-queries with the job's skills (V8); if that also finds nothing it sets
`refused`, and the writer will not claim relevant experience.
"""
from __future__ import annotations

from agent.handoffs import EvidenceRef, FitReport, RequirementMatch
from agent.nodes.base import node, to_thread, tools
from agent.settings import config
from rag.models import rerank_scores, sigmoid
from rag.query import requirements

MAX_EVIDENCE_DOCS = 8


def status_of(grade: float, th: dict) -> str:
    return "proven" if grade >= th["evidence_proven"] else "partial" if grade >= th["evidence_partial"] else "missing"


def fit_score(statuses: list[str], skill_cov: float | None, refused: bool, th: dict) -> float:
    """Shared by the node and evals/runners/fit_calibration.py, which tunes the thresholds."""
    if refused:
        return 0.0
    req = sum({"proven": 1.0, "partial": 0.5, "missing": 0.0}[s] for s in statuses) / max(1, len(statuses))
    w = th.get("fit_requirement_weight", 0.6)
    return round(w * req + (1 - w) * (req if skill_cov is None else skill_cov), 3)


async def _match(req: str, th: dict) -> RequirementMatch:
    res = await tools().call("fit_matcher", "search_evidence",
                             {"requirement": req[:2000], "k": 5, "filters": {"exclude_types": ["proposal"]}})
    hits = res.get("hits", [])
    if not hits:
        return RequirementMatch(requirement=req, status="missing", note="no evidence found")
    scores = await to_thread(rerank_scores, req[:700], [f"{h['title']}. {h['snippet']}" for h in hits])
    graded = sorted(zip(hits, [sigmoid(s) for s in scores]), key=lambda x: -x[1])
    best = graded[0][1]
    status = status_of(best, th)
    ev = [EvidenceRef(evidence_id=h["evidence_id"], type=h["type"], title=h["title"], snippet=h["snippet"],
                      score=round(g, 3)) for h, g in graded[:3] if g >= th["evidence_partial"]]
    return RequirementMatch(requirement=req, status=status, evidence=ev, note=f"best evidence grade {best:.2f}")


@node("fit_matcher")
async def fit_matcher(state: dict) -> dict:
    j, th = state["job"], config()["thresholds"]
    reqs = requirements(j, config()["rag"]["max_requirements"])
    matrix = [await _match(r, th) for r in reqs]
    refused = False
    if all(m.status == "missing" for m in matrix) and j.get("skills"):
        retry = [await _match(f"experience with {s}", th) for s in j["skills"][:4]]
        if any(m.status != "missing" for m in retry):
            matrix += [m for m in retry if m.status != "missing"]
        else:
            refused = True
    skills = await tools().call("fit_matcher", "list_skills", {})
    mine = {s["skill"].lower() for s in skills.get("skills", [])}
    job_skills = list(dict.fromkeys(j.get("skills") or []))
    matched = [s for s in job_skills if s.lower() in mine]
    missing = [s for s in job_skills if s.lower() not in mine]
    skill_cov = len(matched) / len(job_skills) if job_skills else None
    score = fit_score([m.status for m in matrix], skill_cov, refused, th)
    ev_ids: list[str] = []
    for m in matrix:
        for e in m.evidence:
            if e.evidence_id not in ev_ids:
                ev_ids.append(e.evidence_id)
    evidence = {}
    for eid in ev_ids[:MAX_EVIDENCE_DOCS]:
        d = await tools().call("fit_matcher", "get_evidence", {"id": eid})
        evidence[eid] = {k: d.get(k) for k in ("evidence_id", "type", "title", "text", "skills", "skills_implied",
                                               "project_id", "client_industry", "date")}
    reasons = [f"{sum(m.status == 'proven' for m in matrix)}/{len(matrix)} requirements proven, "
               f"{sum(m.status == 'partial' for m in matrix)} partial",
               f"skills matched: {', '.join(matched) or 'none'}" + (f"; missing: {', '.join(missing)}" if missing else "")]
    if refused:
        reasons.append("no portfolio evidence supports this job (corrective retrieval found nothing)")
    rep = FitReport(score=score, matrix=matrix, matched_skills=matched, missing_skills=missing,
                    evidence_ids=list(evidence), reasons=reasons, refused=refused)
    return {"fit_report": rep.model_dump(), "evidence": evidence}
