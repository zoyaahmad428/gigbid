"""Evaluation gate for CI: offline checks that fail the build when quality drops.

    python -m evals.runners.ci_gate                    # compare with evals/baselines/ci_gate.json
    python -m evals.runners.ci_gate --update-baseline  # accept the current numbers (commit the file)

No LLM key or network is needed beyond the embedding/reranker models (cached in CI), so it runs
on every push and pull request. What it checks, on the labelled dev sets:

  retrieval     the deployed retriever (`rag.variant`) on every "bid" dev job: Recall@5, MRR, nDCG@10
  injection     the rule-based injection detector on all dev posts: recall on injected posts and
                the false-positive rate on clean ones (the Prompt Guard model adds to this at runtime)
  scam          the Risk Assessor's scam rules: recall on scam posts, false-positive rate on others
  verification  claim verification on the held-out ("test") claims with the configured thresholds:
                fabricated claims caught, true claims wrongly removed

A metric fails when it is worse than the baseline by more than the tolerance (0.02). The
LLM-dependent evaluations (bid decisions end to end, judge calibration, attack success rate)
need the API key and run separately (step 7).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "evals/baselines/ci_gate.json"
RESULT = ROOT / "evals/results/ci_gate.json"
PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
TOLERANCE = 0.02
LOWER_IS_BETTER = {"injection.false_positive_rate", "scam.false_positive_rate", "verification.true_claims_removed"}


def dev() -> list[dict]:
    out = []
    for p in PERSONAS:
        with open(ROOT / f"evals/datasets/dev_{p}.jsonl", encoding="utf-8") as f:
            out += [json.loads(line) for line in f]
    return out


def post_text(job: dict) -> str:
    return f"{job['title']}\n{job['description']}\n" + "\n".join(job.get("screening_questions") or [])


def rates(pred: list[bool], gold: list[bool]) -> dict:
    pos = [p for p, g in zip(pred, gold) if g]
    neg = [p for p, g in zip(pred, gold) if not g]
    return {"recall": round(sum(pos) / max(1, len(pos)), 4), "false_positive_rate": round(sum(neg) / max(1, len(neg)), 4),
            "n_positive": len(pos), "n_negative": len(neg)}


def retrieval() -> dict:
    from agent.settings import config
    from evals.runners.rag_eval import evaluate, variant_configs
    cfg = variant_configs()[config()["rag"]["variant"]]
    rows = []
    for p in PERSONAS:
        _, r = evaluate(cfg, p, None)
        rows += r
    n = len(rows)
    return {"variant": cfg.name, "n_jobs": n, "recall@5": round(sum(r["recall@5"] for r in rows) / n, 4),
            "mrr": round(sum(r["mrr"] for r in rows) / n, 4), "ndcg@10": round(sum(r["ndcg@10"] for r in rows) / n, 4)}


def injection(items: list[dict]) -> dict:
    from agent.guards import detect_injection
    return rates([detect_injection(post_text(x["job"]))[0] for x in items], [bool(x["has_injection"]) for x in items])


def scam(items: list[dict]) -> dict:
    from agent.nodes.risk_assessor import SCAM_RULES
    hit = [any(re.search(rx, post_text(x["job"]), re.I) for _, rx, _ in SCAM_RULES) for x in items]
    return rates(hit, [bool(x["is_scam"]) for x in items])


def verification() -> dict:
    from agent.settings import config
    from evals.runners.verify_eval import build_claims, metrics, run
    from rag.ingest.corpus import load_persona
    from rag.pipeline import Config, Pipeline
    rng = random.Random(4016)                                # the same claims as verify_eval
    th = config()["rag"]["verify"]
    cfg = Config(name="verify_retrieval", query="post", retrieval="hybrid")
    rows = []
    for p in PERSONAS:
        docs = load_persona(p)
        claims = [c for c in build_claims(p, rng) if c["split"] == "test"]
        rows += run(claims, Pipeline(docs, cfg), {d.evidence_id: d for d in docs}, th)
    m = metrics(rows)
    return {"thresholds": th, "n": m["n"], "fabrication_recall": m["fabrication_recall"],
            "true_claims_removed": m["true_claims_removed"], "not_a_claim_accuracy": m["not_a_claim_accuracy"]}


def flatten(res: dict) -> dict[str, float]:
    return {f"{sec}.{k}": v for sec, d in res.items() if isinstance(d, dict)
            for k, v in d.items() if isinstance(v, float) and not k.startswith("n_")}


def compare(now: dict[str, float], base: dict[str, float]) -> list[tuple[str, float | None, float, bool]]:
    out = []
    for k, v in sorted(now.items()):
        b = base.get(k)
        if b is None:
            out.append((k, None, v, True))
            continue
        worse = (v - b) if k in LOWER_IS_BETTER else (b - v)
        out.append((k, b, v, worse <= TOLERANCE))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--skip", nargs="*", default=[], choices=["retrieval", "verification"])
    a = ap.parse_args()
    items = dev()
    t0 = time.time()
    res = {"injection": injection(items), "scam": scam(items)}
    if "retrieval" not in a.skip:
        res["retrieval"] = retrieval()
    if "verification" not in a.skip:
        res["verification"] = verification()
    res["seconds"] = round(time.time() - t0, 1)
    RESULT.write_text(json.dumps(res, indent=1) + "\n")
    now = flatten(res)
    if a.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"tolerance": TOLERANCE, "metrics": now}, indent=1) + "\n")
        print(f"baseline updated: {BASELINE.relative_to(ROOT)}")
        return
    base = json.loads(BASELINE.read_text())["metrics"] if BASELINE.exists() else {}
    rows = compare(now, base)
    lines = ["| Metric | Baseline | Now | |", "|---|---|---|---|"]
    lines += [f"| {k} | {'—' if b is None else b} | {v} | {'ok' if ok else '**worse**'} |" for k, b, v, ok in rows]
    report = "\n".join(["### Evaluation gate", "", *lines, "",
                        f"Tolerance {TOLERANCE}; lower is better for: {', '.join(sorted(LOWER_IS_BETTER))}."])
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(report + "\n")
    failed = [k for k, _, _, ok in rows if not ok]
    if failed:
        print(f"\nGATE FAILED: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
