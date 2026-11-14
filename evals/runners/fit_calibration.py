"""Calibrate the Fit Matcher's thresholds on the dev sets (tune half), report on the other half.

The Fit Matcher grades each requirement's best evidence with the cross-encoder and turns the
grades into a fit score (agent/nodes/fit_matcher.py: status_of, fit_score). The Decision node bids
when fit >= thresholds.minimum_fit_score. The first hand-set thresholds (proven 0.5, partial 0.25)
came from nowhere: the cross-encoder's grades for genuinely relevant evidence are mostly 0.05-0.3,
so 4 of 5 "bid" jobs ended at watch or skip in the first end-to-end runs.

Positives: gold_action == "bid". Negatives: skipped for "no fit" or "missing required skills"
(budget and scam skips are not about fit and are excluded). Objective: balanced accuracy of
"fit >= min_fit" on the tune half. Raw grades are computed once per job; the sweep is offline.

    python -m evals.runners.fit_calibration
"""
from __future__ import annotations

import hashlib
import itertools
import json
import time
from pathlib import Path

from agent.nodes.fit_matcher import fit_score, status_of
from agent.settings import config
from mcp_servers.portfolio.tools import Filters, store_for
from rag.models import rerank_scores, sigmoid
from rag.query import requirements

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
NEG_REASONS = {"no fit", "missing required skills"}


def split_of(job_id: str) -> str:
    return "tune" if int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % 2 == 0 else "test"


def best_grade(store, req: str) -> float:
    hits = store.search(req[:2000], 5, Filters(exclude_types=["proposal"])).hits
    if not hits:
        return 0.0
    return max(sigmoid(s) for s in rerank_scores(req[:700], [f"{h.title}. {h.snippet}" for h in hits]))


def features(persona: str) -> list[dict]:
    store = store_for(persona)
    mine = {s.skill.lower() for s in store.skills().skills}
    rows = []
    for line in open(ROOT / f"evals/datasets/dev_{persona}.jsonl"):
        x = json.loads(line)
        if x["gold_action"] == "bid":
            label = 1
        elif x["skip_reason"] in NEG_REASONS:
            label = 0
        else:
            continue
        j = x["job"]
        reqs = requirements(j, config()["rag"]["max_requirements"])
        skills = list(dict.fromkeys(j.get("skills") or []))
        rows.append({"persona": persona, "job_id": j["job_id"], "label": label, "split": split_of(j["job_id"]),
                     "req_grades": [best_grade(store, r) for r in reqs],
                     "retry_grades": [best_grade(store, f"experience with {s}") for s in skills[:4]],
                     "skill_cov": (sum(s.lower() in mine for s in skills) / len(skills)) if skills else None})
    return rows


def predict(r: dict, th: dict) -> tuple[float, bool]:
    st = [status_of(g, th) for g in r["req_grades"]]
    refused = False
    if all(s == "missing" for s in st) and r["retry_grades"]:
        retry = [status_of(g, th) for g in r["retry_grades"]]
        if any(s != "missing" for s in retry):
            st += [s for s in retry if s != "missing"]
        else:
            refused = True
    return fit_score(st, r["skill_cov"], refused, th), refused


def evaluate(rows: list[dict], th: dict) -> dict:
    pos = [r for r in rows if r["label"] == 1]
    neg = [r for r in rows if r["label"] == 0]
    bid = lambda r: predict(r, th)[0] >= th["minimum_fit_score"]
    tpr = sum(bid(r) for r in pos) / max(1, len(pos))
    tnr = sum(not bid(r) for r in neg) / max(1, len(neg))
    refused_pos = sum(predict(r, th)[1] for r in pos) / max(1, len(pos))
    return {"n_pos": len(pos), "n_neg": len(neg), "bid_recall": round(tpr, 4), "nofit_rejected": round(tnr, 4),
            "balanced_accuracy": round((tpr + tnr) / 2, 4), "false_refusals_on_bid": round(refused_pos, 4)}


def main() -> None:
    t0 = time.time()
    cache = ROOT / "data/processed/fit_features.json"     # grades are expensive; the sweep is not
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        rows = [r for p in PERSONAS for r in features(p)]
        cache.write_text(json.dumps(rows))
    print(f"features for {len(rows)} jobs in {time.time() - t0:.0f}s (delete {cache.name} after changing retrieval)")
    cur = config()["thresholds"]
    current = {k: cur[k] for k in ("evidence_proven", "evidence_partial", "minimum_fit_score")}
    current["fit_requirement_weight"] = cur.get("fit_requirement_weight", 0.6)
    grid = [{"evidence_partial": pa, "evidence_proven": pr, "fit_requirement_weight": w, "minimum_fit_score": m}
            for pa, pr, w, m in itertools.product([0.03, 0.05, 0.08, 0.1, 0.15, 0.2, 0.25],
                                                  [0.15, 0.2, 0.3, 0.4, 0.5], [0.3, 0.45, 0.6],
                                                  [round(0.1 + 0.05 * i, 2) for i in range(13)]) if pr > pa]
    tune = [r for r in rows if r["split"] == "tune"]
    test = [r for r in rows if r["split"] == "test"]
    best = max(grid, key=lambda th: (evaluate(tune, th)["balanced_accuracy"], th["minimum_fit_score"]))
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "objective": "balanced accuracy of fit >= min_fit (bid vs no-fit) on the tune half",
           "current": {"thresholds": current, "tune": evaluate(tune, current), "test": evaluate(test, current)},
           "chosen": {"thresholds": best, "tune": evaluate(tune, best), "test": evaluate(test, best),
                      "test_by_persona": {p: evaluate([r for r in test if r["persona"] == p], best) for p in PERSONAS}}}
    path = ROOT / "evals/results/fit_calibration.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("current", "chosen")}, indent=1))
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
