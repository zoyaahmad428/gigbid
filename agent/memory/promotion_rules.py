"""Explicit rules for what may enter long-term memory (assignment R7, C2).

Nothing reaches memory directly from a tool or a model. Two inputs exist:

Style memory, from the user's edits to drafts.
  1. Each approved edit is diffed (old draft -> what the user sent). `edit_signals()`
     turns the diff into candidate rules with deterministic detectors:
       - drop_first_sentence  the user deleted the opening sentence
       - avoid:<phrase>       the user deleted a phrase / word (from a fixed list of
                              cliché families, or any 1-3 word phrase removed whole)
       - shorter / longer     the user cut or added more than 25% of the words
       - no_signoff_name      the user removed the trailing name line
  2. A candidate is PROMOTED to a style rule only if it is supported by at least
     `style_promote_min_edits` (3) of the user's last `style_window` (10) edits and
     contradicted by none (e.g. "shorter" is contradicted by a "longer" edit).
  3. Promoted rules are shown in Settings with the edits that support them; the user
     can delete any rule, which also blocks it from being re-promoted for 30 days.

Outcome memory, from marketplace outcomes of submitted proposals.
  1. Each resolved proposal (replied / interview / hired = engaged, lost / closed =
     not engaged) is reduced to features: job category, opening type (specific or
     generic), length bucket, price position vs. market median, evidence cited (y/n).
     The proposal text itself is not stored.
  2. Per category we keep counts per feature value. A LESSON is promoted when a
     category has >= `outcome_min_proposals` (5) resolved proposals and two values of
     a feature differ in engagement rate by >= `outcome_min_gap` (15 points), each
     value having at least 2 proposals. Lessons are re-derived from counts every time,
     so they disappear when new outcomes erase the gap.
"""
from __future__ import annotations

import difflib
import re

from rag.verify import CITE_RE, named_skills

CLICHES = {
    "passionate": r"\bpassionate\b", "dear hiring manager": r"\bdear hiring manager\b",
    "perfect fit": r"\bperfect fit\b", "hardworking": r"\bhard[- ]?working\b",
    "high quality work": r"\bhigh[- ]quality work\b", "i am an expert": r"\bi am an expert\b",
    "kindly": r"\bkindly\b", "looking forward to hearing": r"\blooking forward to hearing\b",
}
CONTRADICTS = {"shorter": "longer", "longer": "shorter"}


def _sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", t) if s.strip()]


def edit_signals(before: str, after: str) -> list[str]:
    sig: list[str] = []
    b, a = _sentences(before), _sentences(after)
    if (b and a and len(b) > 1 and b[0] not in after and b[1] in after
            and difflib.SequenceMatcher(a=b[0].lower(), b=a[0].lower()).ratio() < 0.6):   # gone, not just edited
        sig.append("drop_first_sentence")
    for name, rx in CLICHES.items():
        if re.search(rx, before, re.I) and not re.search(rx, after, re.I):
            sig.append(f"avoid:{name}")
    bw, aw = before.split(), after.split()
    if bw and len(aw) < 0.75 * len(bw):
        sig.append("shorter")
    elif bw and len(aw) > 1.25 * len(bw):
        sig.append("longer")
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=[w.lower() for w in bw], b=[w.lower() for w in aw]).get_opcodes():
        if op == "delete" and 1 <= i2 - i1 <= 3:
            phrase = re.sub(r"[^\w\s'-]", "", " ".join(bw[i1:i2]).lower()).strip()
            # A style signal only if the phrase is gone from the edited text entirely (deleting one of
            # two mentions is not avoiding it), and not a technology name (that is content, not style).
            if (len(phrase) >= 4 and not phrase.isdigit()
                    and not re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", after.lower())
                    and not named_skills(" ".join(bw[i1:i2]))):
                sig.append(f"avoid:{phrase}")
    if b and len(b[-1].split()) <= 3 and (not a or a[-1] != b[-1]):
        sig.append("no_signoff_name")
    return sorted(set(sig))


def promote_style(edit_history: list[list[str]], blocked: set[str], cfg: dict) -> dict[str, int]:
    """edit_history: signals per edit, oldest first. Returns {rule: support} for promoted rules."""
    window = edit_history[-cfg["style_window"]:]
    support: dict[str, int] = {}
    for sigs in window:
        for s in set(sigs):
            support[s] = support.get(s, 0) + 1
    promoted = {}
    for rule, n in support.items():
        if rule in blocked or n < cfg["style_promote_min_edits"]:
            continue
        contra = CONTRADICTS.get(rule)
        if contra and support.get(contra, 0) > cfg["style_max_contradictions"]:
            continue
        promoted[rule] = n
    return promoted


def describe_style(rule: str) -> str:
    if rule == "drop_first_sentence":
        return "Start directly with the client's problem; drop the warm-up first sentence."
    if rule == "shorter":
        return "Keep proposals short; the user consistently cuts about a quarter of the text."
    if rule == "longer":
        return "Give a bit more detail; the user consistently adds to drafts."
    if rule == "no_signoff_name":
        return "Do not end with a name line."
    if rule.startswith("avoid:"):
        return f"Never use the phrase \"{rule[6:]}\"."
    return rule


def outcome_features(proposal: dict, market_p50: float | None) -> dict:
    text = proposal.get("cover_letter", "")
    words = len(text.split())
    first = _sentences(text)[0].lower() if text else ""
    generic = bool(re.search(r"\b(i am|i'm|dear|hello|hi)\b", first)) or any(re.search(rx, first, re.I)
                                                                            for rx in CLICHES.values())
    bid = proposal.get("bid_amount")
    price = "unknown" if not (bid and market_p50) else ("above_median" if bid > 1.1 * market_p50 else
                                                       "below_median" if bid < 0.9 * market_p50 else "at_median")
    return {"opening": "generic" if generic else "specific",
            "length": "short" if words < 150 else "medium" if words <= 250 else "long",
            "price": price, "cites_evidence": "yes" if CITE_RE.search(text) else "no"}


def promote_outcomes(counts: dict, cfg: dict) -> list[dict]:
    """counts: {feature: {value: [engaged, total]}} for one category. Returns lessons."""
    total = sum(t for v in counts.get("opening", {}).values() for _, t in [v])
    if total < cfg["outcome_min_proposals"]:
        return []
    lessons = []
    for feat, vals in counts.items():
        rates = {v: e / t for v, (e, t) in vals.items() if t >= 2 and v != "unknown"}
        if len(rates) < 2:
            continue
        best, worst = max(rates, key=rates.get), min(rates, key=rates.get)
        gap = rates[best] - rates[worst]
        if gap >= cfg["outcome_min_gap"]:
            lessons.append({"feature": feat, "prefer": best, "over": worst, "gap": round(gap, 3),
                            "n": {best: vals[best][1], worst: vals[worst][1]},
                            "text": f"{feat}: '{best}' got replies {rates[best]:.0%} of the time vs "
                                    f"{rates[worst]:.0%} for '{worst}' (n={vals[best][1]}+{vals[worst][1]})."})
    return lessons
