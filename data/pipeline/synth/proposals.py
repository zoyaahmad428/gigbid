"""~150 past proposals per persona, each for a real historical job.

Every proposal cites only true project facts. What varies is how well it is
targeted (fit), how specific it is, whether it opens generically, and the
price. The outcome (won / replied / ignored) is sampled from a simple model of
those factors, so "outcome-aware" retrieval has a real signal to learn, plus
enough noise that wording alone does not decide the outcome:

    p(reply or win) = sigmoid(c + 2.2*fit + 1.0*specificity - 0.9*generic - 1.0*overprice)
    p(win | engaged) = k * (0.5 + 0.5*fit)

c and k are calibrated so the expected rates match `proposal_outcomes` in config.yaml.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stable_id  # noqa: E402
from schema import EvidenceItem, PastProposal, Provenance  # noqa: E402
from taxonomy import PERSONA_SKILLS  # noqa: E402

GEN = "synth.proposals@1.0.0"
PROV = Provenance(source="synthetic", source_dataset="generator", generator=GEN)

GENERIC_OPENINGS = [
    "Hi, I am a passionate and hardworking professional with {years} years of experience.",
    "Dear Hiring Manager, I have read your job post and I am very interested.",
    "Hello! I am the perfect fit for your project and can start right away.",
    "Hi there, I am an expert in this field and I will deliver high quality work.",
]
SPECIFIC_OPENINGS = [
    "You need {need}; I did something very close for a {industry} client in {year}.",
    "The hard part of \"{title}\" is usually {hard}. That is where I would start.",
    "I read your post about {need}. Here is how I would approach it.",
]
HARD_PARTS = {
    "ai_ml": ["getting evaluation right before anything ships", "keeping answers grounded in your data",
              "handling the cases the model is unsure about"],
    "fullstack": ["keeping the data model simple as features grow", "payments and auth edge cases",
                  "performance once real users arrive"],
    "data_analyst": ["agreeing one definition per metric", "making the numbers match finance",
                     "keeping the report fast as data grows"],
}
CLOSINGS = ["Happy to share more detail on a quick call here on the platform.",
            "Could you tell me what data or access I would start with?",
            "What does success look like for you in the first two weeks?",
            "Let me know if you want a short plan before we start."]


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _fit(job: dict, persona: str, pskills: set[str]) -> float:
    rel = set(job["skills"]) & set().union(*PERSONA_SKILLS.values())
    if not rel:
        return 0.1
    return len(rel & pskills) / len(rel)


def _pick_projects(job: dict, projects: list[dict], k: int, rng: random.Random) -> list[dict]:
    js = set(job["skills"])
    scored = sorted(((len(js & (set(p["skills"]) | set(p["implied_skills"]))), rng.random(), i)
                     for i, p in enumerate(projects)), reverse=True)
    return [projects[i] for ov, _, i in scored[:k] if ov >= 1]     # never cite an unrelated project


def generate(persona: str, corpus: dict, hist_jobs: list[dict], market: dict, n: int,
             targets: dict, rng: random.Random) -> tuple[list[dict], list[dict]]:
    prof, projects = corpus["profile"], corpus["projects"]
    pskills = set(prof["core_skills"])
    for p in projects:
        pskills |= set(p["skills"]) | set(p["implied_skills"])
    fit_jobs = [j for j in hist_jobs if persona in j["persona_fit"]]
    other = [j for j in hist_jobs if persona not in j["persona_fit"]]
    n_fit = min(len(fit_jobs), int(n * 0.8))
    chosen = rng.sample(fit_jobs, n_fit) + rng.sample(other, n - n_fit)
    proposals, evidence = [], []
    for job in chosen:
        fit = _fit(job, persona, pskills)
        specificity = min(1.0, max(0.0, rng.gauss(0.55, 0.25)))
        generic = rng.random() < 0.35
        k = 0 if specificity < 0.25 else rng.choice([1, 1, 2, 2, 3])
        cited = _pick_projects(job, projects, k, rng)
        if generic:
            opening = rng.choice(GENERIC_OPENINGS).format(years=prof["years_experience"])
        else:
            ref = cited[0] if cited else rng.choice(projects)
            need = job["title"].lower().rstrip(".")
            opening = rng.choice(SPECIFIC_OPENINGS).format(
                need=need, title=job["title"], industry=ref["client_industry"], year=ref["start"][:4],
                hard=rng.choice(HARD_PARTS[persona]))
        body = [f"For a {p['client_industry']} client I worked on \"{p['title']}\": I {p['approach'][0]}, "
                f"and we {p['metrics'][0]}." for p in cited]
        if specificity > 0.6 and cited:
            body.append(f"For your project I would first {cited[0]['approach'][0].split(' and ')[0]}, "
                        f"then agree milestones with you.")
        letter = "\n\n".join([opening, *body, rng.choice(CLOSINGS), prof["name"].split()[0]])

        b = job["budget"]
        rate = prof["hourly_rate_usd"]
        if b["type"] == "hourly":
            bid = round(rate * rng.uniform(0.85, 1.15), 2)
            ceiling = b["max"] or rate
        else:
            q = market.get(job["category"], {}).get("posted_fixed_budget_usd", {})
            ref = b["max"] or q.get("p50", 500.0)
            bid = round(ref * rng.uniform(0.8, 1.3), -1) or ref
            ceiling = b["max"] or ref
        overprice = max(0.0, bid / ceiling - 1.0) if ceiling else 0.0

        score = 2.2 * fit + 1.0 * specificity - 0.9 * generic - 1.0 * min(overprice, 2.0)
        posted = dt.datetime.fromisoformat(job["posted_at"])
        submitted = (posted + dt.timedelta(hours=rng.randint(1, 48))).isoformat()
        pid = stable_id("prop", persona, job["job_id"])
        prop = PastProposal(
            proposal_id=pid, persona=persona, job_id=job["job_id"], job_title=job["title"],
            cover_letter=letter, bid_usd=bid, bid_type=b["type"],
            cited_projects=[p["project_id"] for p in cited], specificity=round(specificity, 3),
            generic_opening=generic, outcome="ignored", submitted_at=submitted, provenance=PROV).model_dump()
        prop["fit"] = round(fit, 3)
        prop["_score"] = score
        proposals.append(prop)
        evidence.append(EvidenceItem(
            evidence_id=pid, persona=persona, type="proposal",
            project_id=cited[0]["project_id"] if cited else None, title=f"Proposal: {job['title']}",
            path=f"proposals.jsonl#{pid}", skills=sorted(set(job["skills"]) & pskills), skills_implied=[],
            date=submitted[:10], client_industry=cited[0]["client_industry"] if cited else None,
            outcome="ignored", provenance=PROV).model_dump())
    _assign_outcomes(proposals, evidence, targets, rng)
    return proposals, evidence


def _assign_outcomes(proposals: list[dict], evidence: list[dict], targets: dict, rng: random.Random) -> None:
    """Calibrate the intercept so the expected reply-or-win rate matches the
    config targets, then sample outcomes. Fit, specificity, generic opening and
    price still decide *who* gets replies; the targets only set the base rate."""
    t_engage = targets["won"] + targets["replied"]
    scores = [p.pop("_score") for p in proposals]
    lo, hi = -10.0, 10.0
    for _ in range(60):                                   # bisection on the intercept
        c = (lo + hi) / 2
        if sum(_sigmoid(c + s) for s in scores) / len(scores) > t_engage:
            hi = c
        else:
            lo = c
    p_eng = [_sigmoid(c + s) for s in scores]
    w = [0.5 + 0.5 * p["fit"] for p in proposals]
    k = targets["won"] / (sum(pe * wi for pe, wi in zip(p_eng, w)) / len(w))
    by_id = {e["evidence_id"]: e for e in evidence}
    for p, pe, wi in zip(proposals, p_eng, w):
        u = rng.random()
        p["outcome"] = "won" if u < pe * min(1.0, k * wi) else "replied" if u < pe else "ignored"
        p["p_engage"] = round(pe, 4)
        by_id[p["proposal_id"]]["outcome"] = p["outcome"]
