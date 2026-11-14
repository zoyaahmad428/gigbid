"""Retrieval evaluation on the dev set. Rerun the full comparison with one command:

    python -m evals.runners.rag_eval                              # all variants, all personas
    python -m evals.runners.rag_eval --variants v0_naive v2_hybrid --limit 30
    python -m evals.runners.rag_eval --embeddings nomic-ai/nomic-embed-text-v1.5-Q --variants v0_naive
    python -m evals.runners.rag_eval --combo best=decompose,hybrid,rerank,multivector --ablation

Metrics on dev jobs whose gold action is "bid":
  Recall@5   gold items in the top 5, divided by min(5, number of gold items)
  MRR        1 / rank of the first gold item (top 10)
  nDCG@10    graded relevance (2 = gold top-5, 1 = other documents of the relevant projects, CV)
  ProjRecall@3  share of relevant projects with a document in the top 3 (top 10 if fewer)
On "no fit" skip jobs (corrective variants only): how often retrieval correctly refuses.
Writes evals/results/rag_retrieval[_<tag>].{json,csv,md}.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics as st
import time
from dataclasses import replace
from pathlib import Path

from rag.ingest.corpus import load_persona
from rag.pipeline import Config, Pipeline
from rag.query import requirements
from rag.retriever import REGISTRY  # noqa: F401  (imports every variant module)

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evals/results"
PERSONAS = ["ai_ml", "fullstack", "data_analyst"]


def load_dev(persona: str) -> list[dict]:
    with open(ROOT / f"evals/datasets/dev_{persona}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def variant_configs() -> dict[str, Config]:
    import importlib
    import pkgutil

    import rag.variants as pkg
    out = {}
    for m in pkgutil.iter_modules(pkg.__path__):
        cfg = getattr(importlib.import_module(f"rag.variants.{m.name}"), "CONFIG", None)
        if cfg is not None:
            out[cfg.name] = cfg
    return out


def parse_combo(spec: str) -> Config:
    name, _, parts = spec.partition("=")
    kw: dict = {"name": name}
    for p in filter(None, parts.split(",")):
        if p in ("post", "decompose"):
            kw["query"] = p
        elif p in ("dense", "bm25", "hybrid"):
            kw["retrieval"] = p
        elif p in ("rerank", "outcome", "multivector", "corrective"):
            kw[p] = True
        else:
            raise ValueError(f"unknown component {p!r}")
    return Config(**kw)


def ablations(cfg: Config) -> list[Config]:
    out = []
    if cfg.query == "decompose":
        out.append(replace(cfg, name=f"{cfg.name}-decompose", query="post"))
    if cfg.retrieval == "hybrid":
        out.append(replace(cfg, name=f"{cfg.name}-bm25", retrieval="dense"))
        out.append(replace(cfg, name=f"{cfg.name}-dense", retrieval="bm25"))
    for flag in ("rerank", "outcome", "multivector", "corrective"):
        if getattr(cfg, flag):
            out.append(replace(cfg, name=f"{cfg.name}-{flag}", **{flag: False}))
    return out


def ndcg(ranked: list[str], grades: dict[str, int], k: int = 10) -> float:
    dcg = sum((2 ** grades.get(e, 0) - 1) / math.log2(i + 2) for i, e in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def evaluate(cfg: Config, persona: str, limit: int | None, pool: str = "portfolio") -> tuple[dict, list[dict]]:
    docs = load_persona(persona)
    proj_of = {d.evidence_id: d.project_id for d in docs}
    t0 = time.perf_counter()
    pipe = Pipeline(docs, cfg, pool=pool)
    build_s = time.perf_counter() - t0
    dev = load_dev(persona)
    bid = [x for x in dev if x["gold_action"] == "bid"][:limit]
    nofit = [x for x in dev if x["skip_reason"] == "no fit"][:limit] if cfg.corrective else []
    rows, lat = [], []
    for x in bid:
        t = time.perf_counter()
        r = pipe.search(x["job"], k=10)
        lat.append(time.perf_counter() - t)
        gold, grades = x["relevant_evidence"], x["evidence_grades"]
        top5 = set(r.evidence_ids[:5])
        first = next((i + 1 for i, e in enumerate(r.evidence_ids) if e in gold), None)
        rel_proj = x["relevant_projects"]
        kp = 3 if len(rel_proj) <= 3 else 10
        got_proj = {proj_of.get(e) for e in r.evidence_ids[:kp]}
        n_req = len(requirements(x["job"], 10)) - (1 if x["job"].get("skills") else 0)
        rows.append({
            "variant": cfg.name, "persona": persona, "job_id": x["job"]["job_id"],
            "job_type": "4+ requirements" if n_req >= 4 else "1-3 requirements",
            "budget_type": x["job"]["budget"]["type"], "has_injection": x["has_injection"],
            "recall@5": len(top5 & set(gold)) / min(5, len(gold)) if gold else 0.0,
            "mrr": 1 / first if first else 0.0,
            "ndcg@10": ndcg(r.evidence_ids, grades),
            "proj_recall@3": len(set(rel_proj) & got_proj) / len(rel_proj) if rel_proj else 0.0,
            "refused": r.refused, "latency_s": lat[-1],
        })
    refusals_nofit = [pipe.search(x["job"], k=10).refused for x in nofit]
    summ = summarise(rows)
    summ.update({"variant": cfg.name, "persona": persona, "n_bid_jobs": len(rows), "index_build_s": round(build_s, 2),
                 "config": cfg.__dict__})
    if cfg.corrective:
        summ["false_refusal_rate_on_bid"] = round(sum(r["refused"] for r in rows) / max(1, len(rows)), 4)
        summ["refusal_rate_on_nofit"] = round(sum(refusals_nofit) / max(1, len(refusals_nofit)), 4)
    return summ, rows


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    lat = sorted(r["latency_s"] for r in rows)
    out = {m: round(st.mean(r[m] for r in rows), 4) for m in ("recall@5", "mrr", "ndcg@10", "proj_recall@3")}
    out["latency_p50_s"] = round(lat[len(lat) // 2], 4)
    out["latency_p95_s"] = round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 4)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="*")
    ap.add_argument("--personas", nargs="*", default=PERSONAS)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--combo", action="append", default=[], help="name=component,component,...")
    ap.add_argument("--ablation", action="store_true", help="also run each --combo minus one component")
    ap.add_argument("--embeddings", help="override the embedding model for this run")
    ap.add_argument("--tag", default="", help="suffix for the results files")
    ap.add_argument("--pool", choices=["portfolio", "all"], default="portfolio",
                    help="portfolio: only evidence documents are returned; all: past proposals too")
    a = ap.parse_args()
    if a.embeddings:
        os.environ["RAG_EMBEDDINGS"] = a.embeddings
    from rag.models import embedding_name, reranker_name

    allv = variant_configs()
    cfgs = [allv[v] for v in (a.variants if a.variants is not None else sorted(allv))]
    combos = [parse_combo(c) for c in a.combo]
    cfgs += combos
    if a.ablation:
        for c in combos:
            cfgs += ablations(c)

    summaries, all_rows = [], []
    for cfg in cfgs:
        for p in a.personas:
            s, rows = evaluate(cfg, p, a.limit, a.pool)
            summaries.append(s)
            all_rows += rows
            print(f"{cfg.name:28s} {p:13s} R@5 {s['recall@5']:.3f}  MRR {s['mrr']:.3f}  nDCG@10 {s['ndcg@10']:.3f}  "
                  f"p95 {s['latency_p95_s']:.2f}s" + (f"  refuse(nofit) {s['refusal_rate_on_nofit']:.2f} "
                                                      f"false-refuse {s['false_refusal_rate_on_bid']:.2f}"
                                                      if cfg.corrective else ""), flush=True)
        overall = summarise([r for r in all_rows if r["variant"] == cfg.name])
        summaries.append({"variant": cfg.name, "persona": "ALL", **overall,
                          "n_bid_jobs": sum(1 for r in all_rows if r["variant"] == cfg.name)})
        by_type = {}
        for jt in ("1-3 requirements", "4+ requirements"):
            sub = [r for r in all_rows if r["variant"] == cfg.name and r["job_type"] == jt]
            by_type[jt] = {**summarise(sub), "n": len(sub)}
        summaries[-1]["by_job_type"] = by_type

    tag = f"_{a.tag}" if a.tag else ""
    RESULTS.mkdir(parents=True, exist_ok=True)
    meta = {"embeddings": embedding_name(), "reranker": reranker_name(), "limit": a.limit, "pool": a.pool,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (RESULTS / f"rag_retrieval{tag}.json").write_text(json.dumps({"meta": meta, "summaries": summaries}, indent=2))
    with open(RESULTS / f"rag_retrieval{tag}_rows.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0]))
        w.writeheader()
        w.writerows(all_rows)
    lines = [f"Embeddings `{meta['embeddings']}`, reranker `{meta['reranker']}`, pool `{meta['pool']}`, "
             f"generated {meta['generated_at']}.", "",
             "| Variant | Persona | Recall@5 | MRR | nDCG@10 | ProjRecall@3 | p95 latency (s) |", "|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(f"| {s['variant']} | {s['persona']} | {s['recall@5']:.3f} | {s['mrr']:.3f} | {s['ndcg@10']:.3f} | "
                     f"{s['proj_recall@3']:.3f} | {s['latency_p95_s']:.2f} |")
    (RESULTS / f"rag_retrieval{tag}.md").write_text("\n".join(lines) + "\n")
    print(f"wrote evals/results/rag_retrieval{tag}.*")


if __name__ == "__main__":
    main()
