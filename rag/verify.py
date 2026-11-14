"""Claim verification (B3): split a draft into claims, check each against evidence.

Labels per sentence:
  not_a_claim       questions, greetings, sign-offs, plans ("I would start by..."), statements
                    about the client's project; nothing to verify
  supported         the best evidence passage entails it semantically, every number in the
                    claim appears in that evidence, and every named skill is in the evidence
  partly_supported  semantically close but a detail is missing (a number or a skill only appears
                    elsewhere in the portfolio, or the semantic score is in the middle band)
  unsupported       nothing in the evidence backs it, or it states a number or skill that no
                    evidence contains (the usual shape of a fabricated claim)

`clean_draft()` removes unsupported sentences before the Critic sees the draft;
the Proposal Writer can then rewrite. Thresholds live in agent/config.yaml
(`verify:`) and are chosen on half of the claim set in evals/runners/verify_eval.py
and reported on the other half.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from rag.models import rag_config, rerank_scores, sigmoid
from rag.taxonomy import skills_in_text

TECH_TERMS = Path(__file__).with_name("tech_terms.json")   # built by data/pipeline/clean/tech_terms.py


@lru_cache(maxsize=1)
def _tech_re() -> re.Pattern | None:
    """Technology names from real Upwork skill tags. Case-sensitive on purpose: the vocabulary
    keeps a tag only when job posts write it capitalised, so "Flutter" is a technology and
    "flutter" in prose is not."""
    if not TECH_TERMS.exists():
        return None
    terms = sorted(json.loads(TECH_TERMS.read_text())["terms"], key=len, reverse=True)
    return re.compile(r"(?<![\w.])(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)")


def named_skills(sentence: str) -> set[str]:
    """Taxonomy skills plus any other technology name the sentence mentions."""
    found = set(skills_in_text(sentence))
    rx = _tech_re()
    if rx is not None:
        low = {f.lower() for f in found}
        found |= {m.group(1) for m in rx.finditer(sentence) if m.group(1).lower() not in low}
    return found


SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])|\n+")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
CLAIM_RE = re.compile(
    r"\b(I|I've|I have|I'd built|we|we've|my|our team)\b.{0,60}\b(built|build|worked|work|delivered|cut|reduced|"
    r"raised|increased|improved|led|have|has|had|did|developed|created|designed|implemented|shipped|migrated|"
    r"analy[sz]ed|trained|deployed|helped|automated|integrated|launched|managed|wrote|set up|moved|replaced|"
    r"rebuilt|ran|reached|lowered|grew|saved|found|years? of experience|experience)\b", re.I)
EVIDENCE_ID = r"(?:ev|rev|prop)_[0-9a-f]+"                 # case studies/READMEs/code/CV, reviews, proposals
CITE_RE = re.compile(rf"\s*\[({EVIDENCE_ID}(?:\s*,\s*{EVIDENCE_ID})*)\]")   # "[ev_ab12]" or "[ev_1, rev_2]"
NOT_CLAIM_RE = re.compile(
    r"^(hi|hello|hey|dear|thanks|thank you|best|regards|cheers)\b|\?\s*$|"
    r"\bI (have not|haven't|have never|never|do not have|don't have|have no)\b|\bnot (yet )?(worked|used|done)\b|"
    r"\b(I would|I'd (start|begin|first|suggest|propose|recommend)|I will|I'll|I can start|I plan|we would|"
    r"we will|let me know|happy to|could you|can you|would you|looking forward)\b", re.I)


@dataclass
class ClaimResult:
    sentence: str
    label: str
    evidence_id: str | None = None
    score: float | None = None
    reasons: list[str] | None = None


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.split(text) if s and s.strip()]


def is_claim(sentence: str) -> bool:
    if len(sentence) < 15 or NOT_CLAIM_RE.search(sentence):
        return False
    return bool(CLAIM_RE.search(sentence) or (NUM_RE.search(sentence) and re.search(r"\b(I|we|my)\b", sentence)))


def _passages(evidence: list[dict]) -> list[tuple[str, str]]:
    """(evidence_id, passage) windows of about two sentences, plus each document's title line."""
    out = []
    for e in evidence:
        sents = split_sentences(e["text"])
        for i in range(len(sents)):
            out.append((e["evidence_id"], " ".join(sents[i:i + 2])[:600]))
        if e.get("title"):
            out.append((e["evidence_id"], e["title"]))
    return out


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in NUM_RE.findall(s)}


def verify_claims(draft: str, evidence: list[dict], *, thresholds: dict | None = None) -> list[dict]:
    """evidence: [{"evidence_id", "text", "title"?, "skills"?}] (the documents retrieved for this job)."""
    th = {**rag_config().get("verify", {}), **(thresholds or {})}
    t_sup, t_part = th.get("supported", 0.6), th.get("partly", 0.3)
    passages = _passages(evidence)
    doc_text = {e["evidence_id"]: e["text"] for e in evidence}
    doc_skills = {e["evidence_id"]: set(e.get("skills", [])) | set(e.get("skills_implied", [])) for e in evidence}
    all_text = " ".join(doc_text.values())
    all_skills = set().union(*doc_skills.values()) if doc_skills else set()
    out: list[ClaimResult] = []
    for s in split_sentences(draft):
        # Citation markers are metadata, not content: "[ev_b83609f70b]" must not be read as the
        # numbers 83609 and 70. They decide which document the claim is checked against first.
        cited = [c for m in CITE_RE.findall(s) for c in re.split(r"\s*,\s*", m)]
        body = CITE_RE.sub("", s).strip()
        if not is_claim(body):
            out.append(ClaimResult(s, "not_a_claim"))
            continue
        if not passages:
            out.append(ClaimResult(s, "unsupported", reasons=["no evidence retrieved"]))
            continue
        scores = rerank_scores(body, [p for _, p in passages])
        j = max(range(len(scores)), key=lambda i: scores[i])
        reasons = []
        in_cited = [i for i, (e, _) in enumerate(passages) if e in cited]
        if in_cited:
            jc = max(in_cited, key=lambda i: scores[i])
            if sigmoid(scores[jc]) >= t_part or jc == j:
                j = jc
            else:
                reasons.append(f"cited {sorted(set(cited))} does not support it; best match is {passages[j][0]}")
        eid, best = passages[j][0], sigmoid(scores[j])
        s_check = body
        nums = _numbers(s_check) - {"1", "2", "3"}
        in_doc = _numbers(doc_text[eid])
        missing_nums = {n for n in nums if n not in in_doc}
        nowhere_nums = {n for n in missing_nums if n not in _numbers(all_text)}
        sk = named_skills(s_check)
        doc_low = doc_text[eid].lower()
        missing_sk = {k for k in sk if k not in doc_skills[eid] and k.lower() not in doc_low}
        nowhere_sk = {k for k in missing_sk if k not in all_skills and k.lower() not in all_text.lower()}
        if nowhere_nums:
            reasons.append(f"numbers not in any evidence: {sorted(nowhere_nums)}")
        if nowhere_sk:
            reasons.append(f"skills not in any evidence: {sorted(nowhere_sk)}")
        if missing_nums - nowhere_nums:
            reasons.append(f"numbers only in other documents: {sorted(missing_nums - nowhere_nums)}")
        if missing_sk - nowhere_sk:
            reasons.append(f"skills only in other documents: {sorted(missing_sk - nowhere_sk)}")
        if nowhere_nums or nowhere_sk or best < t_part:
            label = "unsupported"
            if best < t_part:
                reasons.append(f"best evidence match {best:.2f} < {t_part}")
        elif best >= t_sup and not missing_nums and not missing_sk and not (cited and eid not in cited):
            label = "supported"
        else:
            label = "partly_supported"
        out.append(ClaimResult(s, label, eid, round(best, 4), reasons))
    return [asdict(r) for r in out]


def clean_draft(draft: str, results: list[dict]) -> tuple[str, dict]:
    """Remove unsupported sentences. Returns (clean_text, stats)."""
    bad = {r["sentence"] for r in results if r["label"] == "unsupported"}
    kept_paras = []
    for para in re.split(r"\n\s*\n", draft):
        sents = [s for s in split_sentences(para) if s not in bad]
        if sents:
            kept_paras.append(" ".join(sents))
    claims = [r for r in results if r["label"] != "not_a_claim"]
    stats = {"claims": len(claims), "unsupported": sum(r["label"] == "unsupported" for r in claims),
             "partly_supported": sum(r["label"] == "partly_supported" for r in claims),
             "supported": sum(r["label"] == "supported" for r in claims)}
    stats["unsupported_rate"] = round(stats["unsupported"] / stats["claims"], 4) if stats["claims"] else 0.0
    return "\n\n".join(kept_paras), stats
