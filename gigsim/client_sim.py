"""Simulated client: decides what happens to a submitted proposal.

Rule-based and deterministic per proposal (seeded by proposal id). The client
sees only what a real client would see (the proposal and the job) plus its
own hidden preferences. Scoring:

    score = 1.2*must_have_coverage + 0.6*addresses_job + 0.5*evidence
            - 1.0*(generic_opening and dislikes_generic) - 0.3*generic_opening
            - 2.0*overprice - length_penalty
    p_view      = min(1, responsiveness + 0.2)
    p_reply     = responsiveness * sigmoid(2.5*(score - 1.3))    (given viewed)
    p_interview = sigmoid(2.5*(score - 1.6))                     (given reply)

Centred so a specific, well-priced proposal (score ~2.0) gets ~0.85 x
responsiveness, a 40%-overpriced one ~0.4x and a generic one ~0.02x. An earlier
centring saturated near 1 and made price irrelevant (see docs/AI_USAGE.md).
    p_hire      = 0.4 + 0.3*must_have_coverage                   (after the freelancer answers)

Policy violations (contact details, off-platform payment, guarantees) make the
client decline. Scam clients reply fast and try to move the conversation off
the platform, which gives the Client Liaison a scam signal to catch.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field

GENERIC_PATTERNS = [
    r"\bi am (?:a |an )?(?:passionate|hardworking|dedicated|expert|professional)\b",
    r"\bdear (?:hiring manager|sir|madam|client)\b", r"\bi have read your job\b",
    r"\bperfect fit for your project\b", r"\bi can do (?:this|your) (?:job|task|project)\b",
    r"\bhigh quality work\b", r"\bto whom it may concern\b",
]
CONTACT_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)"
    r"|\b(?:whatsapp|telegram|skype|wechat|signal)\b", re.I)
OFFPLATFORM_PAY_RE = re.compile(r"\b(?:paypal|crypto|bitcoin|usdt|bank transfer|wire transfer|western union)\b"
                                r"|\b(?:pay|payment)\s+(?:me\s+)?(?:outside|off)[- ]?(?:the\s+)?platform\b", re.I)
GUARANTEE_RE = re.compile(r"\bguarantee[ds]?\b|\b100\s?% (?:satisfaction|success|results)\b", re.I)
METRIC_RE = re.compile(r"\d+(?:\.\d+)?\s?(?:%|x\b|ms\b|k\b|hours?\b|minutes?\b|days?\b|weeks?\b)|\$\d")
STOP = set("a an the and or of to in for on with at by from is are be as we you your our this that it "
           "need needed looking expert developer help project job work using want".split())


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def violations(text: str) -> list[str]:
    v = []
    if CONTACT_RE.search(text):
        v.append("contact_details")
    if OFFPLATFORM_PAY_RE.search(text):
        v.append("off_platform_payment")
    if GUARANTEE_RE.search(text):
        v.append("guarantee")
    return v


def _mentions(skill: str, text: str) -> bool:
    s = skill.lower()
    variants = {s, s.replace(".js", ""), s.replace(" ", ""), s.replace("-", " ")}
    return any(re.search(r"(?<![\w])" + re.escape(v) + r"(?![\w])", text) for v in variants if v)


@dataclass
class Features:
    must_have_coverage: float
    addresses_job: float
    evidence: float
    generic_opening: bool
    words: int
    overprice: float
    violations: list[str] = field(default_factory=list)
    score: float = 0.0


def features(job: dict, hidden: dict, cover_letter: str, answers: list[str], bid: float) -> Features:
    text = (cover_letter + "\n" + "\n".join(answers)).lower()
    must = hidden.get("must_have_skills") or []
    cov = sum(_mentions(s, text) for s in must) / len(must) if must else 0.5
    title_words = {w for w in re.findall(r"[a-z][a-z0-9+#.]{2,}", job["title"].lower()) if w not in STOP}
    opening = cover_letter[:300].lower()
    addresses = min(1.0, sum(w in opening for w in title_words) / max(1, min(3, len(title_words))))
    first = re.split(r"(?<=[.!?])\s", cover_letter.strip(), maxsplit=1)[0].lower()
    generic = any(re.search(p, first) for p in GENERIC_PATTERNS)
    evidence = min(3, len(METRIC_RE.findall(text))) / 3
    words = len(cover_letter.split())
    ceiling = hidden.get("budget_ceiling_usd") or 0
    overprice = max(0.0, bid / ceiling - 1.0) if ceiling > 0 else 0.0
    return Features(round(cov, 3), round(addresses, 3), round(evidence, 3), generic, words,
                    round(overprice, 3), violations(cover_letter + "\n" + "\n".join(answers)))


def score(f: Features, hidden: dict) -> float:
    length_pen = 0.0
    if f.words < 40:
        length_pen += 0.6
    if f.words > 450:
        length_pen += 0.4
    if hidden.get("prefers_short_proposals") and f.words > 220:
        length_pen += 0.4
    s = (1.2 * f.must_have_coverage + 0.6 * f.addresses_job + 0.5 * f.evidence
         - 1.0 * (f.generic_opening and hidden.get("dislikes_generic_openings", False))
         - 0.3 * f.generic_opening - 2.0 * min(f.overprice, 1.5) - length_pen)
    return round(s, 3)


@dataclass
class Plan:
    """What the client will do, as events at simulated-hour offsets from submission."""
    outcome: str                                   # ignored | declined | replied | interview | scam
    events: list[tuple[float, str, dict]]          # (hours_after_submit, event_type, payload)
    features: Features


def _rng(seed: int, *parts: object) -> random.Random:
    h = hashlib.sha256(":".join(str(p) for p in (seed, *parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def plan_for_submission(seed: int, proposal_id: str, job: dict, hidden: dict, cover_letter: str,
                        answers: list[str], bid: float, cm: dict) -> Plan:
    r = _rng(seed, "submit", proposal_id)
    f = features(job, hidden, cover_letter, answers, bid)
    f.score = score(f, hidden)
    vlo, vhi = cm["view_delay_hours"]
    rlo, rhi = cm["reply_delay_hours"]
    t_view = r.uniform(vlo, vhi)

    if hidden.get("is_scammer"):
        t = r.uniform(0.2, 2.0)
        msg = r.choice([
            "Thanks for applying! I can't share details here. Message me on Telegram @proj_manager_example "
            "and I'll send the brief and payment info.",
            "You're shortlisted. To start, please complete a free test task: build the first full module. "
            "We pay once we review it.",
            "Great. We pay by bank transfer outside the platform to save fees. Send your details to "
            "pay-team@example.com.",
        ])
        return Plan("scam", [(t, "viewed", {}), (t + 0.1, "client_message", {"text": msg, "intent": "scam"})], f)

    if f.violations:
        return Plan("declined", [(t_view, "viewed", {}),
                                 (t_view + r.uniform(0.5, 4), "declined",
                                  {"reason": "policy_violation", "violations": f.violations})], f)

    if r.random() > min(1.0, hidden["responsiveness"] + 0.2):
        return Plan("ignored", [], f)
    events: list[tuple[float, str, dict]] = [(t_view, "viewed", {})]
    p_reply = hidden["responsiveness"] * _sigmoid(2.5 * (f.score - 1.3))
    if r.random() > p_reply:
        return Plan("ignored", events, f)
    t_reply = t_view + r.uniform(rlo, rhi)
    title = job["title"]
    if 0 < f.overprice <= 0.3:
        offer = round(hidden["budget_ceiling_usd"], -1 if hidden["budget_ceiling_usd"] >= 100 else 0)
        events.append((t_reply, "client_message", {
            "intent": "negotiation", "offer_usd": offer,
            "text": f"Thanks for the proposal on \"{title}\". Your approach looks good but it's above our budget. "
                    f"Could you do it for ${offer:g}?"}))
        return Plan("replied", events, f)
    if r.random() < _sigmoid(2.5 * (f.score - 1.6)):
        events.append((t_reply, "client_message", {
            "intent": "interview_request",
            "text": f"Hi, thanks for applying to \"{title}\". I'd like to have a quick call. "
                    f"What times work for you over the next few days?"}))
        return Plan("interview", events, f)
    q = r.choice(["How long do you think this would take?", "Have you done something similar before? Any examples?",
                  "What would you need from us to get started?"])
    events.append((t_reply, "client_message", {"intent": "question", "text": f"Thanks for applying. {q}"}))
    return Plan("replied", events, f)


PRICE_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
SLOT_RE = re.compile(r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\butc\b"
                     r"|\b\d{4}-\d{2}-\d{2}t?\d{0,2}", re.I)


def plan_for_message(seed: int, proposal_id: str, n_msg: int, state: str, hidden: dict, text: str,
                     f_cov: float, cm: dict) -> tuple[list[tuple[float, str, dict]], str | None]:
    """Client response to a freelancer message. Returns (events, new_status)."""
    r = _rng(seed, "msg", proposal_id, n_msg)
    v = violations(text)
    if v:
        return [(r.uniform(1, 6), "declined", {"reason": "policy_violation", "violations": v})], "lost"
    if hidden.get("is_scammer"):
        return [(r.uniform(0.2, 1), "client_message",
                 {"intent": "scam", "text": "Please just contact me directly on WhatsApp, it's faster."})], None
    t = r.uniform(*cm["reply_delay_hours"])
    if state == "negotiation":
        m = PRICE_RE.search(text)
        price = float(m.group(1).replace(",", "")) if m else None
        if price is not None and price <= hidden["budget_ceiling_usd"] * 1.02:
            return [(t, "client_message", {"intent": "interview_request",
                                           "text": "That works. Can we do a short call first? What times suit you?"})], "interview"
        return [(t, "declined", {"reason": "price"})], "lost"
    if state == "interview":
        if SLOT_RE.search(text):
            hlo, hhi = cm["hire_delay_hours"]
            if r.random() < 0.4 + 0.3 * f_cov:
                return [(t, "client_message", {"intent": "confirm", "text": "Booked, talk then."}),
                        (t + r.uniform(hlo, hhi), "hired", {})], "interview"
            return [(t, "client_message", {"intent": "confirm", "text": "Booked, talk then."}),
                    (t + r.uniform(hlo, hhi), "declined", {"reason": "chose_another_freelancer"})], "interview"
        return [(t, "client_message", {"intent": "question",
                                       "text": "Could you suggest a couple of specific times (with time zone)?"})], None
    if r.random() < 0.3 + 0.4 * f_cov:
        return [(t, "client_message", {"intent": "interview_request",
                                       "text": "Sounds good. Could we schedule a short call? Suggest some times."})], "interview"
    return [(t, "client_message", {"intent": "question",
                                   "text": "Thanks, that helps. When could you start?"})], None
