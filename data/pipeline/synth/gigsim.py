"""Build the GigSim marketplace data from the cleaned real pool.

  select_jobs     disjoint historical / live / dev draws, balanced by persona
  to_job          real posting -> Job, filling missing fields synthetically
  build_clients   public client records (real signals where they exist) + hidden prefs
  build_live      live stream: real held-out jobs + duplicates, scams, injections
  build_dev       ~200 gold-labelled jobs per persona
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import mask_pii, stable_id  # noqa: E402
from schema import Budget, ClientHidden, ClientPublic, DevItem, Job, JobLabel, Provenance  # noqa: E402
from synth.adversarial import inject, make_scam, paraphrase  # noqa: E402
from synth.persona_content import INDUSTRIES  # noqa: E402
from taxonomy import CATEGORY_OF_PERSONA, PERSONA_SKILLS  # noqa: E402

GEN = "synth.gigsim@1.0.0"
PERSONA_OF_CATEGORY = {v: k for k, v in CATEGORY_OF_PERSONA.items()}
P_IDS = ["ai_ml", "fullstack", "data_analyst"]
SYN = Provenance(source="synthetic", source_dataset="generator", generator=GEN)

SCREENING = {
    "AI & Machine Learning": [
        "Describe an ML or LLM system you shipped to production and how you measured it.",
        "How would you evaluate the quality of the model's answers before launch?",
        "Which model or framework would you start with for this, and why?",
        "Have you worked with a vector database? Which one?"],
    "Web & Full-Stack Development": [
        "Share a link to a web app you built and describe your role.",
        "How do you handle authentication and authorization in your projects?",
        "What is your experience with our stack?",
        "How would you structure the first two weeks of this project?"],
    "Data Analysis & BI": [
        "Describe a dashboard you built and the decision it supported.",
        "How do you make sure your numbers match the finance team's numbers?",
        "Which BI tool do you prefer and why?",
        "Give an example of an analysis that changed what a client did."],
    "Other": ["Describe your recent experience with similar work.",
              "What is your availability for this project?",
              "Why are you a good fit?"],
}


# ------------------------------------------------------------------ selection
def select_jobs(pool: pd.DataFrame, cfg: dict, rng: random.Random) -> dict[str, pd.DataFrame]:
    seed = rng.randrange(2**31)
    shuffled = pool.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    by_cat = {c: g for c, g in shuffled.groupby("category")}
    n_dev = cfg["sizes"]["dev_jobs_per_persona"]
    on_topic_dev = int(round(n_dev * (1 - cfg["dev_set"]["offtopic_rate"] - cfg["dev_set"]["scam_rate"])))
    n_hist, n_live = cfg["sizes"]["historical_jobs"], cfg["sizes"]["live_jobs"]
    # historical: ~27% per persona category, ~19% other; live: same mix
    shares = {"AI & Machine Learning": 0.27, "Web & Full-Stack Development": 0.27,
              "Data Analysis & BI": 0.27, "Other": 0.19}
    take: dict[str, list[pd.DataFrame]] = {"historical": [], "live": [], "dev_pool": []}
    for cat, share in shares.items():
        g = by_cat[cat]
        h, lv = int(round(n_hist * share)), int(round(n_live * share))
        d = on_topic_dev if cat != "Other" else 3 * n_dev        # 'Other' feeds off-topic dev jobs
        need = h + lv + d
        if len(g) < need:
            raise ValueError(f"category {cat}: need {need} jobs, pool has {len(g)}")
        take["historical"].append(g.iloc[:h])
        take["live"].append(g.iloc[h:h + lv])
        take["dev_pool"].append(g.iloc[h + lv:need])
    out = {k: pd.concat(v).reset_index(drop=True) for k, v in take.items()}
    out["historical"] = out["historical"].iloc[:n_hist]
    return out


# ------------------------------------------------------------------ jobs
def connects_table(pool: pd.DataFrame) -> dict:
    """Median real Connects cost by budget bucket, from postings that report it."""
    real = pool[pool["connects_cost"].notna()].copy()
    real["bucket"] = real.apply(_bucket, axis=1)
    return real.groupby("bucket")["connects_cost"].median().round().astype(int).to_dict()


def _bucket(r) -> str:
    v = r["budget_max"] if r["budget_max"] == r["budget_max"] else None
    if r["budget_type"] == "hourly":
        return "h_unknown" if v is None else ("h_low" if v < 25 else "h_mid" if v < 60 else "h_high")
    return "f_unknown" if v is None else ("f_low" if v < 200 else "f_mid" if v < 1000 else "f_high")


def _s(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


def to_job(r: pd.Series, rng: random.Random, ctable: dict, client_id: str,
           posted_at: str | None = None) -> dict:
    synthetic_fields = ["client_id"]
    questions: list[str] = []
    if rng.random() < 0.45:
        bank = SCREENING.get(r["category"], SCREENING["Other"])
        questions = rng.sample(bank, rng.randint(1, min(3, len(bank))))
        synthetic_fields.append("screening_questions")
    connects = r["connects_cost"]
    if connects != connects or connects is None:
        connects = int(ctable.get(_bucket(r), 8))
        synthetic_fields.append("connects_cost")
    if posted_at is not None:
        synthetic_fields.append("posted_at")
    elif bool(r["posted_at_approx"]):
        synthetic_fields.append("posted_at (approximate: relative age anchored to an assumed scrape date)")
    nz = lambda v: None if v != v else float(v)  # noqa: E731
    job = Job(
        job_id=stable_id("job", r["source_dataset"], r["raw_id"]),
        title=r["title"], description=r["description"], skills=list(r["skills"]),
        category=r["category"], persona_fit=list(r["persona_fit"]),
        budget=Budget(type=r["budget_type"], min=nz(r["budget_min"]), max=nz(r["budget_max"])),
        duration=_s(r["duration"]), experience_level=_s(r["experience_level"]),
        screening_questions=questions, client_id=client_id,
        client_country=r["client_country"] if isinstance(r["client_country"], str) else None,
        posted_at=posted_at or r["posted_at"], connects_cost=int(connects),
        provenance=Provenance(source="real", source_dataset=r["source_dataset"],
                              synthetic_fields=synthetic_fields))
    return job.model_dump()


# ------------------------------------------------------------------ clients
def _hidden(client_id: str, skills: list[str], budget: dict, verified: bool, rating: float | None,
            rng: random.Random, market: dict, category: str, scammer: bool = False) -> dict:
    must = rng.sample(skills, min(len(skills), rng.choice([1, 1, 2]))) if skills else []
    nice = [s for s in skills if s not in must][:3]
    if budget.get("max"):
        ceiling = budget["max"] * rng.uniform(1.0, 1.25)
    else:
        m = market.get(category, {})
        key = "contract_hourly_rate_usd" if budget["type"] == "hourly" else "posted_fixed_budget_usd"
        ceiling = m.get(key, {}).get("p75", 50 if budget["type"] == "hourly" else 500)
    resp = 0.30 + 0.25 * verified + 0.15 * (rating is not None and rating >= 4.8) + rng.uniform(-0.1, 0.1)
    return ClientHidden(
        client_id=client_id, must_have_skills=must, nice_to_have_skills=nice,
        budget_ceiling_usd=round(float(ceiling), 2),
        dislikes_generic_openings=rng.random() < 0.65, prefers_short_proposals=rng.random() < 0.4,
        responsiveness=round(min(0.95, max(0.05, resp)), 3), is_scammer=scammer,
        provenance=SYN).model_dump()


def _past_contracts(contracts: pd.DataFrame, idx: list[int]) -> list[dict]:
    out = []
    for i in idx:
        c = contracts.iloc[i]
        title, _ = mask_pii(str(c["job_title"]))
        out.append({"title": title, "start": str(c["start_date"])[:10], "end": str(c["end_date"])[:10],
                    "total_paid_usd": round(float(c["total_paid"]), 2),
                    "hours": round(float(c["total_hours"]), 1),
                    "hourly_rate_usd": None if c["hourly_rate"] != c["hourly_rate"] else float(c["hourly_rate"]),
                    "source": "real", "source_dataset": "asaniczka/freelance-contracts-dataset-1-3-million-entries"})
    return out


def build_clients(jobs_rows: list[tuple[pd.Series, dict]], contracts: pd.DataFrame, market: dict,
                  rng: random.Random) -> tuple[list[dict], list[dict]]:
    """jobs_rows: (cleaned real row, job dict) for every real job that needs a client.
    60k postings carry real client signals and get their own client; 50k postings
    are assigned to synthetic clients (with repeats) or to an existing client."""
    public: dict[str, dict] = {}
    hidden: dict[str, dict] = {}
    cptr = 0
    synth_ids: list[str] = []

    def new_contracts(n: int) -> list[dict]:
        nonlocal cptr
        idx = list(range(cptr, min(cptr + n, len(contracts))))
        cptr += len(idx)
        return _past_contracts(contracts, idx)

    for r, job in jobs_rows:
        real_signals = r["c_payment_verified"] is not None and r["c_payment_verified"] == r["c_payment_verified"]
        if real_signals:
            cid = stable_id("cli", "real", r["raw_id"])
            fb = int(r["c_feedback_num"]) if r["c_feedback_num"] == r["c_feedback_num"] else 0
            hires = fb + rng.randint(0, max(1, fb // 4))
            posted = max(1, hires + rng.randint(0, max(2, hires // 2)))
            rating = None if r["c_rating"] != r["c_rating"] else round(float(r["c_rating"]), 2)
            spent = 0.0 if r["c_spent"] != r["c_spent"] else float(r["c_spent"])
            pub = ClientPublic(
                client_id=cid, country=job["client_country"], payment_verified=bool(r["c_payment_verified"]),
                total_spent_usd=spent, jobs_posted=posted, hires=hires,
                hire_rate=round(hires / posted, 3), rating=rating, feedback_count=fb,
                past_contracts=new_contracts(min(hires, 5)),
                member_since=f"{rng.randint(2012, 2023)}-{rng.randint(1, 12):02d}-01",
                industry=rng.choice(INDUSTRIES),
                provenance=Provenance(source="real", source_dataset=r["source_dataset"],
                                      synthetic_fields=["client_id", "jobs_posted", "hires", "hire_rate",
                                                        "member_since", "industry", "past_contracts linkage"]))
        else:
            if synth_ids and rng.random() < 0.35:                 # repeat poster
                cid = rng.choice(synth_ids)
                job["client_id"] = cid
                public[cid]["jobs_posted"] += 1
                public[cid]["hire_rate"] = round(public[cid]["hires"] / public[cid]["jobs_posted"], 3)
                continue
            cid = stable_id("cli", "syn", r["raw_id"])
            synth_ids.append(cid)
            verified = rng.random() < 0.66                        # 60k: 65.4% payment verified
            hires = int(rng.choice([0, 0, 1, 2, 3, 5, 8, 13, 21, 40]))
            posted = max(1, hires + rng.randint(0, 6))
            rating = None if hires == 0 else round(min(5.0, max(3.0, rng.gauss(4.8, 0.3))), 2)
            spent = 0.0 if hires == 0 else round(float(np.exp(rng.gauss(7.5, 1.8))), 2)
            pub = ClientPublic(
                client_id=cid, country=job["client_country"], payment_verified=verified,
                total_spent_usd=spent, jobs_posted=posted, hires=hires, hire_rate=round(hires / posted, 3),
                rating=rating, feedback_count=hires, past_contracts=new_contracts(min(hires, 5)),
                member_since=f"{rng.randint(2012, 2024)}-{rng.randint(1, 12):02d}-01",
                industry=rng.choice(INDUSTRIES), provenance=SYN)
        job["client_id"] = cid
        public[cid] = pub.model_dump()
        hidden[cid] = _hidden(cid, job["skills"], job["budget"], pub.payment_verified, pub.rating,
                              rng, market, job["category"])
    return list(public.values()), list(hidden.values())


def scam_client(rng: random.Random, country: str | None) -> tuple[dict, dict]:
    cid = stable_id("cli", "scam", rng.random())
    pub = ClientPublic(client_id=cid, country=country, payment_verified=rng.random() < 0.1,
                       total_spent_usd=0.0, jobs_posted=rng.randint(1, 3), hires=0, hire_rate=0.0,
                       rating=None, feedback_count=0, past_contracts=[],
                       member_since=f"2026-{rng.randint(8, 10):02d}-01", industry=rng.choice(INDUSTRIES),
                       provenance=SYN).model_dump()
    hid = ClientHidden(client_id=cid, must_have_skills=[], nice_to_have_skills=[], budget_ceiling_usd=0.0,
                       dislikes_generic_openings=False, prefers_short_proposals=False, responsiveness=0.9,
                       is_scammer=True, provenance=SYN).model_dump()
    return pub, hid


def scam_job(rng: random.Random, persona: str | None, posted_at: str) -> tuple[dict, dict, dict, dict]:
    pattern, title, body = make_scam(rng, persona)
    country = rng.choice(["United States", "United Kingdom", "Nigeria", "India", None])
    pub, hid = scam_client(rng, country)
    skills = sorted(PERSONA_SKILLS[persona])[:2] if persona else []
    hourly = rng.random() < 0.3
    budget = (Budget(type="hourly", min=40.0, max=120.0) if hourly
              else Budget(type="fixed", min=None, max=float(rng.choice([0, 5, 2500, 5000]) or 0) or None))
    job = Job(job_id=stable_id("job", "scam", title, posted_at), title=title, description=body, skills=skills,
              category=CATEGORY_OF_PERSONA.get(persona, "Other"), persona_fit=[persona] if persona else [],
              budget=budget, duration="Less than 1 month", experience_level="Entry level",
              screening_questions=[], client_id=pub["client_id"], client_country=country,
              posted_at=posted_at, connects_cost=rng.choice([2, 4, 6]),
              provenance=Provenance(source="synthetic", source_dataset="generator",
                                    generator="synth.adversarial.scam@1.0.0")).model_dump()
    label = JobLabel(job_id=job["job_id"], is_scam=True, scam_pattern=pattern).model_dump()
    return job, label, pub, hid


def injected_copy(rng: random.Random, job: dict) -> tuple[dict, dict]:
    j = dict(job)
    title, desc, qs, itype, where = inject(rng, j["title"], j["description"], list(j["screening_questions"]))
    j.update(title=title, description=desc, screening_questions=qs)
    j["job_id"] = stable_id("job", "inj", job["job_id"])
    j["provenance"] = Provenance(source="synthetic", source_dataset=job["provenance"]["source_dataset"],
                                 generator="synth.adversarial.injection@1.0.0",
                                 derived_from=job["job_id"]).model_dump()
    return j, JobLabel(job_id=j["job_id"], has_injection=True, injection_type=itype,
                       injection_location=where).model_dump()


# ------------------------------------------------------------------ live stream
def build_live(live_jobs: list[dict], cfg: dict, rng: random.Random) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    lc = cfg["live_stream"]
    start = dt.datetime.fromisoformat(lc["start"].replace("Z", "+00:00"))
    span = lc["days"] * 86400

    def ts() -> str:
        return (start + dt.timedelta(seconds=rng.randrange(span))).isoformat()

    stream, labels, pubs, hids = [], [], [], []
    for j in live_jobs:
        j["posted_at"] = ts()
        if "posted_at" not in j["provenance"]["synthetic_fields"]:
            j["provenance"]["synthetic_fields"].append("posted_at")
        stream.append(j)
        labels.append(JobLabel(job_id=j["job_id"]).model_dump())
    n = len(live_jobs)

    for j in rng.sample(live_jobs, int(round(n * lc["injection_rate"]))):
        inj, lab = injected_copy(rng, j)
        inj["posted_at"] = ts()
        stream.append(inj)
        labels.append(lab)

    for _ in range(int(round(n * lc["scam_rate"]))):
        job, lab, pub, hid = scam_job(rng, rng.choice(P_IDS + [None]), ts())
        stream.append(job)
        labels.append(lab)
        pubs.append(pub)
        hids.append(hid)

    for j in rng.sample(live_jobs, int(round(n * lc["duplicate_rate"]))):
        d = dict(j)
        kind = "exact" if rng.random() < 0.5 else "paraphrase"
        if kind == "paraphrase":
            d["title"], d["description"] = paraphrase(rng, j["title"], j["description"])
        later = dt.datetime.fromisoformat(j["posted_at"]) + dt.timedelta(hours=rng.randint(2, 96))
        d["posted_at"] = later.isoformat()
        d["job_id"] = stable_id("job", "dup", j["job_id"], kind)
        d["provenance"] = Provenance(source="synthetic", source_dataset=j["provenance"]["source_dataset"],
                                     generator=f"synth.adversarial.duplicate_{kind}@1.0.0",
                                     derived_from=j["job_id"]).model_dump()
        stream.append(d)
        labels.append(JobLabel(job_id=d["job_id"], is_duplicate=True, dup_of=j["job_id"], dup_kind=kind).model_dump())

    order = sorted(range(len(stream)), key=lambda i: stream[i]["posted_at"])
    return [stream[i] for i in order], [labels[i] for i in order], pubs, hids


# ------------------------------------------------------------------ dev set
def persona_skillset(corpus: dict) -> set[str]:
    s = set(corpus["profile"]["core_skills"])
    for p in corpus["projects"]:
        s |= set(p["skills"]) | set(p["implied_skills"])
    return s


TYPE_PRIORITY = {"case_study": 4, "code": 3, "readme": 2, "review": 1, "cv": 0}


def relevant_for(job: dict, corpus: dict) -> tuple[list[str], list[str], dict[str, int]]:
    """Gold evidence for a bid job.

    A project's relevance is its skill overlap with the job (named or implied
    skills), each skill weighted by rarity across this persona's projects
    (log(N/df)), so "Python", which nearly every AI project has, counts for little.
    Grade 2: every project within 80% of the best score (ties are *all* relevant;
    an earlier version broke ties by recency, which no retriever can reproduce).
    Grade 1: projects within 40%, and the CV when it names a job skill.
    `relevant_evidence` lists the grade-2 projects' documents, case studies first;
    Recall@5 is computed against min(5, len(gold)).
    """
    js = set(job["skills"])
    projects = corpus["projects"]
    n = len(projects)
    df: dict[str, int] = {}
    for p in projects:
        for sk in set(p["skills"]) | set(p["implied_skills"]):
            df[sk] = df.get(sk, 0) + 1
    w = {sk: math.log((n + 1) / (c + 0.5)) for sk, c in df.items()}
    scored = sorted(((sum(w[sk] for sk in js & (set(p["skills"]) | set(p["implied_skills"]))), p["project_id"])
                     for p in projects), reverse=True)
    best = scored[0][0] if scored else 0.0
    if best <= 0:
        return [], [], {}
    top = [pid for sc, pid in scored if sc >= 0.8 * best]
    near = [pid for sc, pid in scored if 0.4 * best <= sc < 0.8 * best]
    rank = {pid: i for i, pid in enumerate(top)}
    items = [e for e in corpus["evidence"] if e["project_id"] in rank and e["type"] != "proposal"]
    items.sort(key=lambda e: (rank[e["project_id"]], -TYPE_PRIORITY[e["type"]], e["evidence_id"]))
    gold = [e["evidence_id"] for e in items]
    grades = {e: 2 for e in gold}
    for e in corpus["evidence"]:
        if e["project_id"] in near and e["type"] != "proposal":
            grades[e["evidence_id"]] = 1
    if js & set(corpus["profile"]["core_skills"]):
        for e in corpus["evidence"]:
            if e["type"] == "cv":
                grades[e["evidence_id"]] = 1
    return top, gold, grades


def fair_band(job: dict, market: dict) -> dict:
    m = market.get(job["category"], market.get("Other", {}))
    b = job["budget"]
    if b["type"] == "hourly":
        q = m.get("contract_hourly_rate_usd", {})
        lo, hi = q.get("p25", 15.0), q.get("p75", 45.0)
        if b.get("max"):
            hi = min(hi, b["max"]) if b["max"] >= lo else b["max"]
            lo = min(lo, hi)
        return Budget(type="hourly", min=round(lo, 2), max=round(hi, 2)).model_dump()
    q = m.get("posted_fixed_budget_usd", {})
    if b.get("max"):
        return Budget(type="fixed", min=round(0.75 * b["max"], 2), max=round(b["max"], 2)).model_dump()
    return Budget(type="fixed", min=q.get("p25", 100.0), max=q.get("p75", 1000.0)).model_dump()


def gold_action(job: dict, persona: str, corpus: dict, is_scam: bool) -> tuple[str, str | None]:
    if is_scam:
        return "skip", "scam"
    if persona not in job["persona_fit"]:
        return "skip", "no fit"
    relevant = set(job["skills"]) & PERSONA_SKILLS[persona]
    covered = relevant & persona_skillset(corpus)
    if relevant and len(covered) / len(relevant) < 0.5:
        return "skip", "missing required skills"
    if set(job["skills"]) & set(corpus["profile"]["does_not_do"]):
        return "skip", "outside stated skills"
    prof, b = corpus["profile"], job["budget"]
    if b.get("max"):
        if b["type"] == "hourly" and b["max"] < 0.6 * prof["hourly_rate_usd"]:
            return "skip", "budget below floor"
        if b["type"] == "fixed" and b["max"] < prof["min_fixed_budget_usd"]:
            return "skip", "budget below floor"
    return "bid", None


def build_dev(persona: str, on_topic: list[dict], off_topic: list[dict], corpus: dict, cfg: dict,
              market: dict, rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    n = cfg["sizes"]["dev_jobs_per_persona"]
    n_scam = int(round(n * cfg["dev_set"]["scam_rate"]))
    n_inj = int(round(n * cfg["dev_set"]["injection_rate"]))
    n_off = n - n_scam - len(on_topic)
    items, pubs, hids = [], [], []
    inj_ids = {j["job_id"] for j in rng.sample(on_topic, n_inj)}
    for j in on_topic + rng.sample(off_topic, n_off):
        has_inj = j["job_id"] in inj_ids
        if has_inj:
            j, _ = injected_copy(rng, j)
        action, reason = gold_action(j, persona, corpus, False)
        projs, ev, grades = relevant_for(j, corpus) if action == "bid" else ([], [], {})
        items.append(DevItem(job=Job(**j), persona=persona, gold_action=action, skip_reason=reason,
                             is_scam=False, has_injection=has_inj, relevant_projects=projs,
                             relevant_evidence=ev, evidence_grades=grades,
                             fair_price_band=Budget(**fair_band(j, market))).model_dump())
    for _ in range(n_scam):
        ts = dt.datetime(2026, 10, rng.randint(1, 28), rng.randint(0, 23), tzinfo=dt.timezone.utc).isoformat()
        job, _, pub, hid = scam_job(rng, persona, ts)
        pubs.append(pub)
        hids.append(hid)
        items.append(DevItem(job=Job(**job), persona=persona, gold_action="skip", skip_reason="scam",
                             is_scam=True, has_injection=False, relevant_projects=[], relevant_evidence=[],
                             fair_price_band=Budget(type=job["budget"]["type"], min=0.0, max=0.0)).model_dump())
    rng.shuffle(items)
    return items, pubs, hids
