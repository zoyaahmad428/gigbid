"""Evaluate claim verification against ground-truth claims built from the persona facts.

    python -m evals.runners.verify_eval

For each project in data/personas/<p>/facts.jsonl we write claims a proposal
might make, with known labels:
  true          "For a fintech client I cut forecast error from 18% to 11%."     -> supported
  wrong_number  the same claim with a changed number                            -> unsupported
  wrong_skill   "...built with Kubernetes" (a skill no document mentions)       -> unsupported
  invented      a project the persona never did                                 -> unsupported
  plan          "I would start by agreeing on success metrics."                 -> not_a_claim
The verifier sees what the Writer would see: the top documents retrieved for the
claim (hybrid retrieval over the portfolio, past proposals excluded).
Thresholds are chosen on a "tune" half (seeded split by project) and reported on
the "test" half. Writes evals/results/verify_claims.json.
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from rag.ingest.corpus import load_persona
from rag.pipeline import Config, Pipeline
from rag.verify import verify_claims

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
FAKE_SKILLS = ["Kubernetes", "Rust", "Salesforce", "SAP", "Unity", "Flutter", "Solidity", "Kotlin"]
INVENTED = ["I built the recommendation engine behind a top-10 streaming app, lifting watch time by 27%.",
            "I led a team of 12 engineers at a Fortune 500 bank for four years.",
            "I migrated a hospital network's records system to the cloud, serving 2 million patients.",
            "I have 15 years of experience with SAP and Salesforce integrations."]
PLANS = ["I would start by agreeing on how we measure success.", "Could you share a sample of the data?",
         "I can start on Monday and send a short plan first.", "Happy to jump on a quick call here.",
         # honest disclaimers are not claims of experience and must never be removed
         "I have not worked with Kubernetes in production yet, so I would keep the deployment simple.",
         "I haven't used Snowflake before; the SQL itself carries over."]


def _perturb(claim: str, rng: random.Random) -> str | None:
    nums = list(re.finditer(r"\d+", claim))
    if not nums:
        return None
    m = rng.choice(nums)
    v = int(m.group(0))
    new = v + rng.choice([7, 11, 13, 19]) if v < 1000 else v * 3
    return claim[:m.start()] + str(new) + claim[m.end():]


def build_claims(persona: str, rng: random.Random) -> list[dict]:
    facts = [json.loads(line) for line in open(ROOT / f"data/personas/{persona}/facts.jsonl")]
    out = []
    for f in facts:
        split = "tune" if rng.random() < 0.5 else "test"
        for metric in f["metrics"]:
            true = f"For a {f['client_industry']} client I {metric}."
            # Half the claims carry a citation marker, as the Proposal Writer's drafts do; the
            # verifier must read "[ev_b83609f70b]" as a reference, not as the numbers 83609 and 70.
            cite = rng.random() < 0.5
            out.append({"claim": true, "kind": "true", "gold": "supported", "split": split, "cite": cite})
            bad = _perturb(true, rng)
            if bad:
                out.append({"claim": bad, "kind": "wrong_number", "gold": "unsupported", "split": split, "cite": cite})
        sk = rng.choice(FAKE_SKILLS)
        out.append({"claim": f"I built the {f['title'].split(' for ')[0].lower()} with {sk}.", "kind": "wrong_skill",
                    "gold": "unsupported", "split": split})
    for i, c in enumerate(INVENTED):
        out.append({"claim": c, "kind": "invented", "gold": "unsupported", "split": "tune" if i % 2 else "test"})
    for i, c in enumerate(PLANS):
        out.append({"claim": c, "kind": "plan", "gold": "not_a_claim", "split": "tune" if i % 2 else "test"})
    return out


def run(claims: list[dict], pipe: Pipeline, docs_by_id: dict, th: dict) -> list[dict]:
    rows = []
    for c in claims:
        r = pipe.search({"title": c["claim"], "description": c["claim"], "skills": []}, k=5)
        ev = [{"evidence_id": e, "text": docs_by_id[e].text, "title": docs_by_id[e].title,
               "skills": docs_by_id[e].skills, "skills_implied": docs_by_id[e].skills_implied} for e in r.evidence_ids]
        text = f"{c['claim'][:-1]} [{r.evidence_ids[0]}]." if c.get("cite") and r.evidence_ids else c["claim"]
        res = verify_claims(text, ev, thresholds=th)
        label = "not_a_claim" if all(x["label"] == "not_a_claim" for x in res) else \
            min((x["label"] for x in res if x["label"] != "not_a_claim"),
                key=["unsupported", "partly_supported", "supported"].index)
        rows.append({**c, "pred": label, "score": next((x["score"] for x in res if x["score"] is not None), None)})
    return rows


def metrics(rows: list[dict]) -> dict:
    claims = [r for r in rows if r["gold"] != "not_a_claim"]
    fab = [r for r in claims if r["gold"] == "unsupported"]
    true = [r for r in claims if r["gold"] == "supported"]
    caught = sum(r["pred"] == "unsupported" for r in fab)
    flagged = [r for r in claims if r["pred"] == "unsupported"]
    by_kind = {}
    for k in sorted({r["kind"] for r in rows}):
        sub = [r for r in rows if r["kind"] == k]
        by_kind[k] = {"n": len(sub), "pred": {p: sum(r["pred"] == p for r in sub) for p in
                                              ("supported", "partly_supported", "unsupported", "not_a_claim")}}
        cited = [r for r in sub if r.get("cite")]
        if cited:
            by_kind[k]["cited_unsupported_rate"] = round(sum(r["pred"] == "unsupported" for r in cited) / len(cited), 4)
    return {
        "n": len(rows),
        "fabrication_recall": round(caught / len(fab), 4) if fab else None,           # fabrications removed
        "unsupported_precision": round(sum(r["gold"] == "unsupported" for r in flagged) / len(flagged), 4) if flagged else None,
        "true_claims_removed": round(sum(r["pred"] == "unsupported" for r in true) / len(true), 4) if true else None,
        "true_claims_fully_supported": round(sum(r["pred"] == "supported" for r in true) / len(true), 4) if true else None,
        "not_a_claim_accuracy": round(sum(r["pred"] == "not_a_claim" for r in rows if r["gold"] == "not_a_claim") /
                                      max(1, sum(r["gold"] == "not_a_claim" for r in rows)), 4),
        "by_kind": by_kind,
    }


def main() -> None:
    rng = random.Random(4016)
    cfg = Config(name="verify_retrieval", query="post", retrieval="hybrid")
    all_rows: dict[str, list[dict]] = {}
    grid = [{"supported": s, "partly": p} for s in (0.4, 0.5, 0.6, 0.7, 0.8) for p in (0.1, 0.2, 0.3, 0.4) if p < s]
    data = {}
    for persona in PERSONAS:
        docs = load_persona(persona)
        data[persona] = (Pipeline(docs, cfg), {d.evidence_id: d for d in docs}, build_claims(persona, rng))
    # choose thresholds on the tune split: maximise caught fabrications minus true claims removed
    best, best_obj = None, -1e9
    for th in grid:
        rows = [r for p, (pipe, by, cl) in data.items() for r in run([c for c in cl if c["split"] == "tune"], pipe, by, th)]
        m = metrics(rows)
        obj = (m["fabrication_recall"] or 0) - 2 * (m["true_claims_removed"] or 0)
        if obj > best_obj:
            best, best_obj = th, obj
    for p, (pipe, by, cl) in data.items():
        all_rows[p] = run([c for c in cl if c["split"] == "test"], pipe, by, best)
    test_rows = [r for rows in all_rows.values() for r in rows]
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "chosen_thresholds": best,
           "objective": "fabrication_recall - 2 * true_claims_removed (on the tune split)",
           "test": metrics(test_rows), "test_by_persona": {p: metrics(r) for p, r in all_rows.items()},
           "examples": [r for r in test_rows if r["pred"] != r["gold"]][:15]}
    (ROOT / "evals/results").mkdir(parents=True, exist_ok=True)
    (ROOT / "evals/results/verify_claims.json").write_text(json.dumps(out, indent=2))
    t = out["test"]
    print(f"thresholds {best}  fabrications caught {t['fabrication_recall']}  true claims removed "
          f"{t['true_claims_removed']}  fully supported {t['true_claims_fully_supported']}  plans ok "
          f"{t['not_a_claim_accuracy']}")
    print(json.dumps(t["by_kind"], indent=1))


if __name__ == "__main__":
    main()
