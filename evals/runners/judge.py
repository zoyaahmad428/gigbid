"""LLM judge for proposal quality, and its calibration against your own labels (B4).

    python -m evals.runners.judge --build            # pick the 30 calibration items (blind, shuffled)
    python -m evals.runners.label                    # YOU label them in the terminal (about 30 minutes)
    python -m evals.runners.judge --score            # the judge scores the same items (and all B drafts)
    python -m evals.runners.judge --kappa            # agreement: Cohen's kappa, judge vs you

Items: the verified drafts of R&D question B (both retrieval arms), topped up with drafts from the
injection study's shipped-prompt arm, 30 in all, shuffled, with the arm hidden from the labeller.
The rubric is agent/prompts/judge.md (1-5 and "would send as is"). The judge model defaults to
gpt-oss-120b: another model family than the Qwen writer, so it does not grade its own style.

Writes evals/judge/items.json, evals/judge/judge_scores.json and evals/results/judge_calibration.json.
Your labels are evals/judge/human_labels.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

from pydantic import BaseModel, Field

from evals.runners.common import RESULTS, gold_by_id

ROOT = Path(__file__).resolve().parents[2]
JDIR = ROOT / "evals/judge"
ITEMS, HUMAN, SCORES = JDIR / "items.json", JDIR / "human_labels.json", JDIR / "judge_scores.json"
N_ITEMS = 30


class Verdict(BaseModel):
    score: int = Field(ge=1, le=5)
    send: bool
    reason: str


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def build() -> None:
    gold = gold_by_id()
    pool = []
    for arm in ("v0_naive", "combined"):
        for r in rows(RESULTS / "runs" / f"prop_{arm}.jsonl"):
            if r.get("draft"):
                pool.append({"source": f"prop_{arm}", "job_id": r["job_id"], "letter": r["draft"]})
    for r in rows(RESULTS / "runs" / "injection.jsonl"):
        if r.get("draft_L2") and len(pool) < N_ITEMS + 12:
            pool.append({"source": "injection_L2", "job_id": r["job_id"], "letter": r["draft_L2"]})
    rng = random.Random(4016)
    main = [p for p in pool if p["source"].startswith("prop_")]
    extra = [p for p in pool if not p["source"].startswith("prop_")]
    chosen = (main + rng.sample(extra, max(0, min(len(extra), N_ITEMS - len(main)))))[:N_ITEMS]
    rng.shuffle(chosen)
    items = []
    for i, p in enumerate(chosen):
        j = gold[p["job_id"]]["job"]
        items.append({"item": f"i{i + 1:02d}", **p, "title": j["title"], "description": j["description"][:1500]})
    JDIR.mkdir(parents=True, exist_ok=True)
    ITEMS.write_text(json.dumps({"rubric": "agent/prompts/judge.md", "items": items}, indent=1) + "\n")
    print(f"{len(items)} items -> {ITEMS.relative_to(ROOT)}  (sources: "
          f"{ {s: sum(i['source'] == s for i in items) for s in {i['source'] for i in items}} })")


def score(model: str, extra_sources: bool) -> None:
    os.environ.setdefault("GIGBID_RATE_WAIT", str(8 * 3600))
    from agent.llm import structured
    items = json.loads(ITEMS.read_text())["items"]
    if extra_sources:                                        # every B draft, for the results table
        gold = gold_by_id()
        have = {(i["source"], i["job_id"]) for i in items}
        for arm in ("v0_naive", "combined"):
            for r in rows(RESULTS / "runs" / f"prop_{arm}.jsonl"):
                if r.get("draft") and (f"prop_{arm}", r["job_id"]) not in have:
                    j = gold[r["job_id"]]["job"]
                    items.append({"item": f"x_{arm}_{r['job_id']}", "source": f"prop_{arm}", "job_id": r["job_id"],
                                  "letter": r["draft"], "title": j["title"], "description": j["description"][:1500]})
    out = json.loads(SCORES.read_text()) if SCORES.exists() else {"model": model, "scores": {}}
    os.environ["MODEL_PROFILE"] = "open"
    from agent import settings
    settings.profile()["nodes"]["judge"] = model
    for it in items:
        if it["item"] in out["scores"]:
            continue
        v, u = structured("judge", Verdict, {"title": it["title"], "description": it["description"],
                                             "letter": it["letter"]})
        out["scores"][it["item"]] = {**v.model_dump(), "source": it["source"], "job_id": it["job_id"],
                                     "tokens": u.get("tokens_in", 0) + u.get("tokens_out", 0)}
        SCORES.write_text(json.dumps(out, indent=1) + "\n")
        print(f"{it['item']:<28} {it['source']:<14} score={v.score} send={v.send}", flush=True)


def kappa(a: list, b: list, weights: str | None = None, cats: list | None = None) -> float | None:
    cats = cats or sorted(set(a) | set(b))
    k = len(cats)
    if not a or k < 2:
        return None
    idx = {c: i for i, c in enumerate(cats)}
    n = len(a)
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1
    ra = [sum(row) for row in obs]
    cb = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    def w(i, j):
        if weights == "quadratic":
            return ((i - j) ** 2) / ((k - 1) ** 2)
        return 0.0 if i == j else 1.0
    num = sum(w(i, j) * obs[i][j] for i in range(k) for j in range(k))
    den = sum(w(i, j) * ra[i] * cb[j] / n for i in range(k) for j in range(k))
    return round(1 - num / den, 4) if den else None


def calibrate() -> dict:
    human = json.loads(HUMAN.read_text())["labels"]
    judge = json.loads(SCORES.read_text())
    ids = [i for i in human if i in judge["scores"]]
    h_s = [human[i]["score"] for i in ids]
    j_s = [judge["scores"][i]["score"] for i in ids]
    h_b = [human[i]["send"] for i in ids]
    j_b = [judge["scores"][i]["send"] for i in ids]
    disagreements = sorted(({"item": i, "human": human[i], "judge": judge["scores"][i]} for i in ids),
                           key=lambda d: -abs(d["human"]["score"] - d["judge"]["score"]))[:5]
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "judge_model": judge["model"],
           "n": len(ids), "kappa_score_quadratic": kappa(h_s, j_s, "quadratic", [1, 2, 3, 4, 5]),
           "kappa_send": kappa(h_b, j_b, None, [False, True]),
           "exact_agreement": round(sum(x == y for x, y in zip(h_s, j_s)) / max(1, len(ids)), 4),
           "within_one": round(sum(abs(x - y) <= 1 for x, y in zip(h_s, j_s)) / max(1, len(ids)), 4),
           "mean_human": round(sum(h_s) / max(1, len(h_s)), 3), "mean_judge": round(sum(j_s) / max(1, len(j_s)), 3),
           "largest_disagreements": disagreements}
    (RESULTS / "judge_calibration.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "largest_disagreements"}, indent=1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--kappa", action="store_true")
    ap.add_argument("--all-drafts", action="store_true", help="also score every B draft (for the results table)")
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    a = ap.parse_args()
    os.environ.setdefault("GIGBID_DB", "sqlite")
    os.environ.setdefault("GIGBID_LANGFUSE", "off")
    if a.build:
        build()
    if a.score:
        score(a.model, a.all_drafts)
    if a.kappa:
        calibrate()


if __name__ == "__main__":
    main()
