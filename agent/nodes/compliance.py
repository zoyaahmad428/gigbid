"""Compliance Guard: enforce the policy pack. Rules first (they cannot be talked out of it),
then a small model for what rules miss. Can block, with a reason shown to the user."""
from __future__ import annotations

import re

from agent.guards import CANARY_RE
from agent.handoffs import ComplianceVerdict
from agent.llm import structured
from agent.nodes.base import node, to_thread, usage_entry
from agent.settings import ROOT

POLICY = (ROOT / "data/policy_pack/marketplace_policy.md")
RULES = [
    ("contact_details", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)|"
                                   r"\b(whatsapp|telegram|skype|wechat)\b|https?://\S+", re.I)),
    ("off_platform_payment", re.compile(r"\b(paypal|crypto|bitcoin|bank transfer|wire transfer|outside (the )?platform|"
                                        r"off[- ]platform)\b", re.I)),
    ("guarantee", re.compile(r"\bguarantee[ds]?\b|\b100\s?% (satisfaction|success|results)\b", re.I)),
    ("injection_canary", CANARY_RE),
]


def _sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", t) if s.strip()]


def apply_rules(text: str) -> tuple[str, list[str], list[str]]:
    """Remove sentences that break a rule. Returns (clean, violations, removed sentences)."""
    violations, removed, kept_paras = [], [], []
    for para in re.split(r"\n\s*\n", text):
        kept = []
        for s in _sentences(para):
            hit = [name for name, rx in RULES if rx.search(s)]
            if hit:
                violations += hit
                removed.append(s.strip())
            else:
                kept.append(s.strip())
        if kept:
            kept_paras.append(" ".join(kept))
    return "\n\n".join(kept_paras), sorted(set(violations)), removed


async def check(text: str, injection_excerpt: str | None) -> tuple[ComplianceVerdict, dict]:
    clean, violations, removed = apply_rules(text)
    v, u = await to_thread(structured, "compliance", ComplianceVerdict, {
        "policy": POLICY.read_text() if POLICY.exists() else "", "text": clean,
        "injection_excerpt": injection_excerpt or "none"})
    v.violations = sorted(set(violations) | set(v.violations))
    v.redactions = removed + v.redactions
    v.cleaned_text = clean
    if not clean.strip():
        v.allowed, v.reason = False, "Nothing left after removing policy violations."
    return v, u


@node("compliance")
async def compliance(state: dict) -> dict:
    d = dict(state["draft"])
    inj = state.get("risk_report", {}).get("injection_excerpt")
    v, u = await check(d["cover_letter"], inj)
    total = {k: u.get(k, 0) for k in ("tokens_in", "tokens_out", "cost_usd", "calls")}
    answers = []
    for a in d.get("screening_answers", []):
        clean, viol, removed = apply_rules(a)
        answers.append(clean or "I would be glad to discuss this on a call here on the platform.")
        v.violations = sorted(set(v.violations) | set(viol))
        v.redactions += removed
    d.update(cover_letter=v.cleaned_text or d["cover_letter"], screening_answers=answers,
             status="compliant" if v.allowed else "blocked")
    out = {"draft": d, "compliance": v.model_dump(), "usage": usage_entry("compliance", total),
           "drafts": [{**d, "stage": "compliance", "violations": v.violations}]}
    if not v.allowed:
        out.update(status="blocked", status_reason=f"Compliance Guard blocked the draft: {v.reason}")
    return out
