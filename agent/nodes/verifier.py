"""Claim verification between the Writer and the Critic (B3): unsupported claims are removed."""
from __future__ import annotations

from agent.nodes.base import node, to_thread
from rag.verify import clean_draft, verify_claims


def _verify(text: str, evidence: list[dict]) -> tuple[str, list[dict], dict]:
    results = verify_claims(text, evidence)
    clean, stats = clean_draft(text, results)
    return clean, results, stats


@node("verifier")
async def verifier(state: dict) -> dict:
    d = dict(state["draft"])
    ev = [{"evidence_id": k, **v} for k, v in state.get("evidence", {}).items()]
    clean, results, stats = await to_thread(_verify, d["cover_letter"], ev)
    answers, a_stats = [], {"claims": 0, "unsupported": 0}
    for a in d.get("screening_answers", []):
        ca, r, s = await to_thread(_verify, a, ev)
        answers.append(ca or "I have not done this exact work before; here is how I would approach it.")
        results += r
        for k in a_stats:
            a_stats[k] += s[k]
    total = {"claims": stats["claims"] + a_stats["claims"], "unsupported": stats["unsupported"] + a_stats["unsupported"],
             "supported": sum(r["label"] == "supported" for r in results),
             "partly_supported": sum(r["label"] == "partly_supported" for r in results)}
    total["unsupported_rate"] = round(total["unsupported"] / total["claims"], 4) if total["claims"] else 0.0
    citations = [{"sentence": r["sentence"], "evidence_ids": [r["evidence_id"]], "label": r["label"]}
                 for r in results if r["label"] in ("supported", "partly_supported") and r.get("evidence_id")]
    d.update(cover_letter=clean, screening_answers=answers, citations=citations, status="verified")
    prev = state.get("verification", {})
    ver = {"results": results, "stats": total, "stats_first_draft": prev.get("stats_first_draft", total)}
    return {"draft": d, "verification": ver, "drafts": [{**d, "stage": "verified", "verification": total}]}
