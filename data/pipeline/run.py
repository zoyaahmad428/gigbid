"""Build the whole GigSim dataset:  python data/pipeline/run.py [--refresh]

Stages (each writes to the paths in config.yaml):
  1. clean real postings into one pool              data/interim/jobs_pool.parquet (cached)
  2. market price statistics                        data/gigsim/market_stats.json
  3. scam lexicon (aggregate stats only)            data/gigsim/scam_lexicon.json
  4. disjoint job selection for history/live/dev
  5. persona corpora                                data/personas/<persona>/
  6. jobs + clients (public and hidden)             data/gigsim/
  7. past proposals per persona                     data/personas/<persona>/proposals.jsonl
  8. live stream with duplicates/scams/injections   data/gigsim/jobs_live.jsonl
  9. dev sets                                       evals/datasets/dev_<persona>.jsonl
 10. validation + build report                      data/pipeline/reports/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clean.fraud_lexicon import SLUG as TRIAL1_SLUG, fraud_lexicon  # noqa: E402
from clean.jobs import clean_pool  # noqa: E402
from clean.market import contracts_sample, market_stats  # noqa: E402
from common import load_config, read_jsonl, rng, write_json, write_jsonl  # noqa: E402
from synth import gigsim, personas, proposals  # noqa: E402
from synth.persona_content import PERSONAS  # noqa: E402
from validate import validate  # noqa: E402

SUCCESS_SLUG = "sarveshchhetri/freelancer-success-earnings-and-churn-prediction"
SUCCESS_SKILL = {"ai_ml": "AI & Machine Learning", "fullstack": "Web Development",
                 "data_analyst": "Data Science & Analytics"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def persona_priors(persona: str, market: dict, raw: Path) -> dict:
    cat = PERSONAS[persona]["category"]
    m = market[cat]
    rate = round(m["contract_hourly_rate_usd"]["p75"] / 5) * 5
    floor = max(100, round(m["posted_fixed_budget_usd"]["p25"] / 10) * 10)
    fs = pd.read_csv(raw / "freelancer_success" / "freelancer_gig_success_dataset.csv")
    fs = fs[(fs["primary_skill"] == SUCCESS_SKILL[persona]) & (fs["has_portfolio"] == 1)]
    return {
        "hourly_rate_usd": float(rate), "min_fixed_budget_usd": float(floor),
        "job_success_score": int(round(np.nanpercentile(fs["job_success_rate"], 75))),
        "jobs_completed": int(fs["total_jobs_completed"].dropna().median()),
        "sources": {
            "hourly_rate_usd": f"p75 contract hourly rate, {cat}, freelance_contracts_1_3m",
            "min_fixed_budget_usd": f"p25 posted fixed budget, {cat}, upwork_all_jobs_200k",
            "job_success_score": f"p75 job_success_rate, {SUCCESS_SKILL[persona]} with portfolio, {SUCCESS_SLUG}",
            "jobs_completed": f"median total_jobs_completed, same subset, {SUCCESS_SLUG}",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="rebuild cached real-data stages")
    args = ap.parse_args()
    cfg = load_config()
    P = cfg["paths"]
    raw, interim, gs, pdir, devdir, rep = (P["raw"], P["interim"], P["gigsim"], P["personas"],
                                          P["dev_set"], P["reports"])
    report: dict = {"pipeline_version": cfg["pipeline_version"], "seed": cfg["seed"]}

    # 1. pool ----------------------------------------------------------------
    pool_path, pool_stats_path = interim / "jobs_pool.parquet", interim / "jobs_pool_stats.json"
    if args.refresh or not pool_path.exists():
        log("cleaning real job postings")
        pool, st = clean_pool(raw)
        interim.mkdir(parents=True, exist_ok=True)
        pool.to_parquet(pool_path)
        write_json(pool_stats_path, st)
    pool = pd.read_parquet(pool_path)
    for col in ("tags", "skills", "persona_fit"):
        pool[col] = pool[col].map(list)
    report["cleaning"] = json.loads(pool_stats_path.read_text())
    log(f"pool: {len(pool)} real postings")

    # 2. market --------------------------------------------------------------
    mpath = interim / "market_stats.json"
    if args.refresh or not mpath.exists():
        log("computing market statistics")
        market, mst = market_stats(raw)
        write_json(mpath, {"stats": market, "meta": mst})
    mobj = json.loads(mpath.read_text())
    market = mobj["stats"]
    write_json(gs / "market_stats.json", {
        "by_category": market, "meta": mobj["meta"],
        "provenance": {"source": "real", "source_dataset": [
            "asaniczka/all-jobs-on-upwork-200k-plus",
            "asaniczka/freelance-contracts-dataset-1-3-million-entries"]}})
    report["market"] = mobj["meta"]

    # 3. scam lexicon --------------------------------------------------------
    lpath = interim / "scam_lexicon.json"
    if args.refresh or not lpath.exists():
        log("computing scam lexicon from trial_1")
        lex, lst = fraud_lexicon(raw, top=60, min_count=8)
        write_json(lpath, {"phrases": lex, "meta": lst})
    lobj = json.loads(lpath.read_text())
    write_json(gs / "scam_lexicon.json", {**lobj, "provenance": {
        "source": "real", "source_dataset": TRIAL1_SLUG,
        "note": "aggregate n-gram statistics only; no source text is redistributed"}})
    report["scam_lexicon"] = lobj["meta"]

    # 3b. technology vocabulary for claim verification (rag/tech_terms.json) ---
    from clean.tech_terms import OUT as TECH_OUT, build as build_tech_terms
    if args.refresh or not TECH_OUT.exists():
        log("building technology vocabulary from Upwork 60k skill tags (~4 min)")
        TECH_OUT.write_text(json.dumps(build_tech_terms(), indent=1) + "\n")
    report["tech_terms"] = len(json.loads(TECH_OUT.read_text())["terms"])

    # 4. selection -----------------------------------------------------------
    sel = gigsim.select_jobs(pool, cfg, rng(cfg, "select"))
    ctable = gigsim.connects_table(pool)
    log("selected: " + ", ".join(f"{k}={len(v)}" for k, v in sel.items()))

    # 5. personas ------------------------------------------------------------
    corpora = {}
    for p in gigsim.P_IDS:
        pri = persona_priors(p, market, raw)
        corpora[p] = personas.generate_persona(p, pdir / p, pri, rng(cfg, f"persona:{p}"))
        log(f"persona {p}: {len(corpora[p]['projects'])} projects, {len(corpora[p]['evidence'])} docs")

    # 6. jobs + clients ------------------------------------------------------
    jr = rng(cfg, "jobs")
    pairs: dict[str, list] = {k: [] for k in sel}
    for k, df in sel.items():
        for _, r in df.iterrows():
            pairs[k].append((r, gigsim.to_job(r, jr, ctable, client_id="")))
    contracts = contracts_sample(raw, 30000, cfg["seed"])
    all_pairs = pairs["historical"] + pairs["live"] + pairs["dev_pool"]
    clients_pub, clients_hid = gigsim.build_clients(all_pairs, contracts, market, rng(cfg, "clients"))
    hist = [j for _, j in pairs["historical"]]
    log(f"jobs: historical={len(hist)}; clients={len(clients_pub)}")

    # 7. proposals -----------------------------------------------------------
    for p in gigsim.P_IDS:
        props, ev = proposals.generate(p, corpora[p], hist, market, cfg["sizes"]["proposals_per_persona"],
                                       cfg["proposal_outcomes"], rng(cfg, f"proposals:{p}"))
        write_jsonl(pdir / p / "proposals.jsonl", props)
        corpora[p]["evidence"] += ev
        corpora[p]["proposals"] = props
        write_jsonl(pdir / p / "evidence_index.jsonl", corpora[p]["evidence"])
        log(f"proposals {p}: {dict(Counter(x['outcome'] for x in props))}")

    # 8. live stream ---------------------------------------------------------
    live, labels, spub, shid = gigsim.build_live([j for _, j in pairs["live"]], cfg, rng(cfg, "live"))
    clients_pub += spub
    clients_hid += shid

    # 9. dev sets ------------------------------------------------------------
    dev_jobs = [j for _, j in pairs["dev_pool"]]
    dr = rng(cfg, "dev")
    dev = {}
    used: set[str] = set()
    n_on = int(round(cfg["sizes"]["dev_jobs_per_persona"] *
                     (1 - cfg["dev_set"]["offtopic_rate"] - cfg["dev_set"]["scam_rate"])))
    for p in gigsim.P_IDS:
        cat = PERSONAS[p]["category"]
        on = [j for j in dev_jobs if j["category"] == cat and j["job_id"] not in used][:n_on]
        used |= {j["job_id"] for j in on}
        off = [j for j in dev_jobs if j["category"] != cat and j["job_id"] not in used]
        items, dpub, dhid = gigsim.build_dev(p, on, off, corpora[p], cfg, market, dr)
        used |= {it["job"]["job_id"] for it in items}
        clients_pub += dpub
        clients_hid += dhid
        dev[p] = items

    # keep only clients actually referenced
    ref = {j["client_id"] for j in hist + live} | {it["job"]["client_id"] for v in dev.values() for it in v}
    clients_pub = [c for c in clients_pub if c["client_id"] in ref]
    clients_hid = [c for c in clients_hid if c["client_id"] in ref]

    # write ------------------------------------------------------------------
    write_jsonl(gs / "jobs_historical.jsonl", hist)
    write_jsonl(gs / "jobs_live.jsonl", live)
    write_jsonl(gs / "clients.jsonl", clients_pub)
    write_jsonl(gs / "_hidden" / "clients_hidden.jsonl", clients_hid)
    write_jsonl(gs / "_hidden" / "live_labels.jsonl", labels)
    for p, items in dev.items():
        write_jsonl(devdir / f"dev_{p}.jsonl", items)

    # 10. validate + report ---------------------------------------------------
    report["outputs"] = summarise(gs, pdir, devdir)
    problems = validate(cfg)
    report["validation"] = {"ok": not problems, "problems": problems}
    write_json(rep / "build_report.json", report)
    log("validation: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
    for pr in problems[:20]:
        print("  -", pr)
    if problems:
        sys.exit(1)


def _prov_counts(rows: list[dict], key: str = "provenance") -> dict:
    return dict(Counter(r[key]["source"] for r in rows))


def summarise(gs: Path, pdir: Path, devdir: Path) -> dict:
    out: dict = {}
    hist, live = read_jsonl(gs / "jobs_historical.jsonl"), read_jsonl(gs / "jobs_live.jsonl")
    labels = read_jsonl(gs / "_hidden" / "live_labels.jsonl")
    out["jobs_historical"] = {"n": len(hist), "by_source": _prov_counts(hist),
                              "by_category": dict(Counter(j["category"] for j in hist)),
                              "by_dataset": dict(Counter(j["provenance"]["source_dataset"] for j in hist))}
    out["jobs_live"] = {"n": len(live), "by_source": _prov_counts(live),
                        "duplicates": sum(x["is_duplicate"] for x in labels),
                        "scams": sum(x["is_scam"] for x in labels),
                        "injections": sum(x["has_injection"] for x in labels)}
    cl = read_jsonl(gs / "clients.jsonl")
    out["clients"] = {"n": len(cl), "by_source": _prov_counts(cl)}
    for p in gigsim.P_IDS:
        ev = read_jsonl(pdir / p / "evidence_index.jsonl")
        props = read_jsonl(pdir / p / "proposals.jsonl")
        facts = read_jsonl(pdir / p / "facts.jsonl")
        dev = read_jsonl(devdir / f"dev_{p}.jsonl")
        out[f"persona_{p}"] = {
            "projects": len(facts), "documents": dict(Counter(e["type"] for e in ev)),
            "proposals": len(props), "proposal_outcomes": dict(Counter(x["outcome"] for x in props)),
        }
        out[f"dev_{p}"] = {"n": len(dev), "gold_action": dict(Counter(d["gold_action"] for d in dev)),
                           "skip_reasons": dict(Counter(d["skip_reason"] for d in dev if d["skip_reason"])),
                           "scams": sum(d["is_scam"] for d in dev),
                           "injections": sum(d["has_injection"] for d in dev),
                           "job_source": _prov_counts([d["job"] for d in dev])}
    return out


if __name__ == "__main__":
    main()
