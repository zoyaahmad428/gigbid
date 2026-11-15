"""Build the step-7 results tables from the runs files: evals/results/summary.json and RESULTS.md.

    python -m evals.runners.report

Every number comes from a file in evals/results/ (runs/*.jsonl are the per-job rows). Unsupported
claims are measured with one yardstick for every system: each sentence that makes a claim about
past work is checked (rag/verify.py, the shipped thresholds) against the 5 portfolio documents
retrieved for that sentence (hybrid retrieval, past proposals excluded), the setup of
evals/runners/verify_eval.py. So a system is not judged by the evidence it chose to look at.
"""
from __future__ import annotations

import json
import os
import time
from functools import lru_cache

from evals.runners.common import RESULTS, net_seconds, percentile

SCAM_FLAGS = {"free_work_request", "off_platform_payment", "off_platform_contact", "credential_request",
              "scam_pattern"}


def rows(name: str) -> list[dict]:
    p = RESULTS / "runs" / f"{name}.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def load(name: str) -> dict:
    p = RESULTS / name
    return json.loads(p.read_text()) if p.exists() else {}


def prf(pred: list[bool], gold: list[bool]) -> dict:
    tp = sum(p and g for p, g in zip(pred, gold))
    fp = sum(p and not g for p, g in zip(pred, gold))
    fn = sum(g and not p for p, g in zip(pred, gold))
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else 0.0
    return {"n": len(pred), "precision": None if prec is None else round(prec, 4),
            "recall": None if rec is None else round(rec, 4), "f1": round(f1, 4),
            "accuracy": round(sum(p == g for p, g in zip(pred, gold)) / max(1, len(pred)), 4)}


def tokens(r: dict) -> tuple[int, float]:
    t = r.get("tokens") or {}
    return (sum(v.get("tokens_in", 0) + v.get("tokens_out", 0) for v in t.values()),
            sum(v.get("cost_usd", 0) for v in t.values()))


# ------------------------------------------------------------------ claim yardstick
@lru_cache(maxsize=3)
def _pipe(persona: str):
    from rag.ingest.corpus import load_persona
    from rag.pipeline import Config, Pipeline
    docs = load_persona(persona)
    return Pipeline(docs, Config(name="verify_retrieval", query="post", retrieval="hybrid")), {d.evidence_id: d for d in docs}


def unsupported(persona: str, text: str | None) -> dict:
    from rag.verify import is_claim, split_sentences, verify_claims
    from rag.verify import CITE_RE
    if not text:
        return {"claims": 0, "unsupported": 0}
    pipe, by = _pipe(persona)
    claims = uns = 0
    for s in split_sentences(text):
        body = CITE_RE.sub("", s).strip()
        if not is_claim(body):
            continue
        r = pipe.search({"title": body, "description": body, "skills": []}, k=5)
        ev = [{"evidence_id": e, "text": by[e].text, "title": by[e].title, "skills": by[e].skills,
               "skills_implied": by[e].skills_implied} for e in r.evidence_ids]
        res = [x for x in verify_claims(body, ev) if x["label"] != "not_a_claim"]
        if res:
            claims += 1
            uns += any(x["label"] == "unsupported" for x in res)
    return {"claims": claims, "unsupported": uns}


def canary_hit(r: dict, text: str | None) -> bool:
    from evals.runners.injection_eval import canaries_for, followed
    from evals.runners.common import gold_by_id
    if not r.get("has_injection") or not text:
        return False
    return followed(text, canaries_for(gold_by_id()[r["job_id"]]["job"]))


# ------------------------------------------------------------------ sections
def decisions(rs: list[dict] | None = None) -> dict:
    rs = [r for r in (rows("decisions") if rs is None else rs) if not r.get("error")]
    if not rs:
        return {}
    from agent.settings import config
    max_risk = config()["thresholds"]["maximum_risk_score"]
    bid = prf([r["decision"] == "bid" for r in rs], [r["gold_action"] == "bid" for r in rs])
    scam_pred = [(r.get("risk") or 0) > max_risk or bool(SCAM_FLAGS & set(r.get("risk_flags") or [])) for r in rs]
    inj_pred = ["injected_instructions" in (r.get("risk_flags") or []) for r in rs]
    priced = [r for r in rs if r["decision"] == "bid" and r.get("price_in_band") is not None]
    by_kind = {}
    for r in rs:
        k = by_kind.setdefault(r["kind"], {"n": 0, "correct": 0})
        k["n"] += 1
        k["correct"] += (r["decision"] == "bid") == (r["gold_action"] == "bid")
    secs = [s for s in map(net_seconds, rs) if s is not None]
    tok = [tokens(r) for r in rs]
    wrong = [{"job_id": r["job_id"], "kind": r["kind"], "gold": r["gold_action"], "decision": r["decision"],
              "reason": r.get("reason"), "fit": r.get("fit"), "risk": r.get("risk"), "flags": r.get("risk_flags")}
             for r in rs if (r["decision"] == "bid") != (r["gold_action"] == "bid")]
    return {"n_jobs": len(rs), "bid_or_skip": bid,
            "by_kind": {k: {**v, "accuracy": round(v["correct"] / v["n"], 3)} for k, v in sorted(by_kind.items())},
            "scam": prf(scam_pred, [r["is_scam"] for r in rs]),
            "injection_detection": prf(inj_pred, [r["has_injection"] for r in rs]),
            "price_in_fair_band": {"n": len(priced), "rate": round(sum(r["price_in_band"] for r in priced) /
                                                                   max(1, len(priced)), 4)},
            "latency_s": {"p50": percentile(secs, 0.5), "p95": percentile(secs, 0.95), "n": len(secs)},
            "tokens_per_job": round(sum(t for t, _ in tok) / len(tok), 1),
            "usd_per_job": round(sum(c for _, c in tok) / len(tok), 6), "wrong_decisions": wrong}


def decisions_v2() -> dict:
    """The decisions run after the injection change (regex v2 + risk prompt v2).

    Only the 11 jobs the change can affect were re-run (tag decisions_v2: every job either regex flagged,
    every scam, and the wrong decisions involving an injection); the other 25 keep their first-run rows.
    """
    new = {r["job_id"]: r for r in rows("decisions_v2") if not r.get("error")}
    if not new:
        return {}
    old = {r["job_id"]: r for r in rows("decisions") if not r.get("error")}
    right = lambda r: (r["decision"] == "bid") == (r["gold_action"] == "bid")  # noqa: E731
    changes = [{"job_id": k, "kind": r["kind"], "gold": r["gold_action"], "v1": old[k]["decision"],
                "v2": r["decision"], "v1_risk": old[k].get("risk"), "v2_risk": r.get("risk"),
                "v1_correct": right(old[k]), "v2_correct": right(r)} for k, r in new.items() if k in old]
    merged = [new.get(r["job_id"], r) for r in old.values()]
    return {"rerun_jobs": len(changes), "rerun_correct_v1": sum(c["v1_correct"] for c in changes),
            "rerun_correct_v2": sum(c["v2_correct"] for c in changes),
            "scams_in_rerun": sum(new[c["job_id"]]["is_scam"] for c in changes),
            "scams_bid_on_v2": sum(new[c["job_id"]]["is_scam"] and c["v2"] == "bid" for c in changes),
            "changes": changes, "all_jobs_with_v2": decisions(merged)}


def proposal_quality() -> dict:
    out = {}
    retr = {"v0_naive": next((s for s in load("rag_retrieval_variants.json").get("summaries", [])
                              if s["variant"] == "v0_naive" and s["persona"] == "ALL"), None)}
    v0 = [s for s in load("rag_retrieval_variants.json").get("summaries", []) if s["variant"] == "v0_naive"]
    if v0 and not retr["v0_naive"]:
        n = sum(s["n_bid_jobs"] for s in v0)
        retr["v0_naive"] = {m: round(sum(s[m] * s["n_bid_jobs"] for s in v0) / n, 4) for m in ("recall@5", "ndcg@10")}
    gate = load("ci_gate.json").get("retrieval", {})
    retr["combined"] = {"recall@5": gate.get("recall@5"), "ndcg@10": gate.get("ndcg@10")}
    # the calibrated judge: v2 (agent/prompts/judge_v2.md) when it has scored, else v1
    jfile = next((f for f in ("judge_scores_v2.json", "judge_scores.json") if (RESULTS.parent / "judge" / f).exists()), None)
    judge = load(f"../judge/{jfile}") if jfile else {}
    jscores = (judge or {}).get("scores", {})
    outcomes = load("outcomes.json")
    for arm in ("v0_naive", "combined"):
        rs = [r for r in rows(f"prop_{arm}") if not r.get("error") and r.get("draft")]
        if not rs:
            continue
        first = [r.get("verification_first") or {} for r in rs]
        yard = [unsupported(r["persona"], r["draft"]) for r in rs]
        js = [v["score"] for v in jscores.values() if v["source"] == f"prop_{arm}"]
        secs = [s for s in map(net_seconds, rs) if s is not None]
        tok = [tokens(r) for r in rs]
        out[arm] = {
            "n_proposals": len(rs), "recall@5": (retr.get(arm) or {}).get("recall@5"),
            "ndcg@10": (retr.get(arm) or {}).get("ndcg@10"),
            "unsupported_per_proposal_before_verification": round(sum(f.get("unsupported", 0) for f in first) / len(rs), 3),
            "claims_per_proposal_before_verification": round(sum(f.get("claims", 0) for f in first) / len(rs), 3),
            "unsupported_rate_before_verification": round(sum(f.get("unsupported", 0) for f in first) /
                                                          max(1, sum(f.get("claims", 0) for f in first)), 4),
            "unsupported_per_proposal_after_verification_yardstick": round(sum(y["unsupported"] for y in yard) / len(rs), 3),
            "unsupported_rate_after_verification_yardstick": round(sum(y["unsupported"] for y in yard) /
                                                                   max(1, sum(y["claims"] for y in yard)), 4),
            "judge_score": round(sum(js) / len(js), 3) if js else None, "judge_n": len(js),
            "judge_prompt": (judge or {}).get("prompt", "judge"),
            "gigsim_reply_rate": (outcomes.get(f"prop_{arm}") or {}).get("reply_rate"),
            "gigsim_submitted": (outcomes.get(f"prop_{arm}") or {}).get("submitted"),
            "p95_latency_s": percentile(secs, 0.95), "p50_latency_s": percentile(secs, 0.5),
            "cost_per_proposal_usd": round(sum(c for _, c in tok) / len(tok), 6),
            "tokens_per_proposal": round(sum(t for t, _ in tok) / len(tok), 1),
            "writer_profile": rs[0].get("profile"),
            "by_persona": {p: round(sum(f.get("unsupported", 0) for f, r in zip(first, rs) if r["persona"] == p) /
                                    max(1, sum(r["persona"] == p for r in rs)), 3)
                           for p in sorted({r["persona"] for r in rs})}}
    return out


def orchestration() -> dict:
    from evals.runners.baselines import c_jobs
    jobs = {j["job_id"] for j in c_jobs()}
    for name in ("c_react", "c_supervisor", "c_graph"):     # paired: only jobs every system has finished
        jobs &= {r["job_id"] for r in rows(name)}
    outcomes = load("outcomes.json")
    out = {}
    for name, label in (("c_react", "single ReAct agent"), ("c_supervisor", "prebuilt supervisor"),
                        ("c_graph", "GigBid graph")):
        rs = [r for r in rows(name) if r["job_id"] in jobs]
        if not rs:
            continue
        ok = [r for r in rs if not r.get("error")]
        drafts = [r for r in ok if r.get("draft") and r.get("decision") == "bid"]
        yard = [unsupported(r["persona"], r["draft"]) for r in drafts]
        from agent.nodes.compliance import apply_rules
        viol = [bool(apply_rules(r["draft"])[1]) for r in drafts]
        caught = sum(len((r.get("compliance") or {}).get("violations") or []) > 0 for r in drafts)
        secs = [s for s in map(net_seconds, rs) if s is not None]
        tok = [tokens(r) for r in rs]
        out[name] = {
            "system": label, "n_jobs": len(rs), "failed_runs": len(rs) - len(ok),
            "bid_or_skip_accuracy": round(sum((r.get("decision") == "bid") == (r["gold_action"] == "bid") for r in rs)
                                          / len(rs), 4),
            "scams_bid_on": sum(r["is_scam"] and r.get("decision") == "bid" for r in rs),
            "proposals": len(drafts),
            "unsupported_claims_per_proposal": round(sum(y["unsupported"] for y in yard) / len(drafts), 3) if drafts else None,
            "drafts_with_policy_violations_left": sum(viol),
            "violations_caught_by_compliance": caught,
            "injections_followed": sum(canary_hit(r, r.get("draft")) for r in drafts),
            "gigsim_reply_rate": (outcomes.get(name) or {}).get("reply_rate"),
            "rejected_at_submission": (outcomes.get(name) or {}).get("rejected_at_submission"),
            "tokens_per_job": round(sum(t for t, _ in tok) / len(tok), 1),
            "usd_per_job": round(sum(c for _, c in tok) / len(tok), 6),
            "latency_p50_s": percentile(secs, 0.5), "latency_p95_s": percentile(secs, 0.95),
            "per_job": [{"job_id": r["job_id"], "kind": r["kind"], "decision": r.get("decision"),
                         "error": (r.get("error") or "")[:120]} for r in rs]}
    return out


def budgets(d: dict) -> dict:
    """E5: measured p95 latency and cost against agent/config.yaml budgets.declared."""
    from agent.settings import config
    declared = config()["budgets"]["declared"]
    bids = [r for r in rows("c_graph") if r.get("decision") == "bid" and r.get("draft") and not r.get("error")]
    secs = [s for s in map(net_seconds, bids) if s is not None]
    cost = [tokens(r)[1] for r in bids]
    return {"declared": declared,
            "job_analysed": {"n": d.get("latency_s", {}).get("n"), "p95_s": d.get("latency_s", {}).get("p95"),
                             "usd": d.get("usd_per_job"), "source": "decisions run (shipped open profile)"},
            "proposal": {"n": len(secs), "p95_s": percentile(secs, 0.95),
                         "usd": round(sum(cost) / len(cost), 6) if cost else None,
                         "source": "question C graph runs that ended in a draft (open_20b profile)"}}


def table(headers: list[str], body: list[list]) -> str:
    def fmt(x):
        if x is None:
            return "—"
        if isinstance(x, float):
            if 0 < abs(x) < 0.01:                       # costs per job: keep two significant digits
                return f"{x:.2g}"
            return f"{x:.3f}".rstrip("0").rstrip(".") if abs(x) < 100 else f"{x:,.0f}"
        return str(x)
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] +
                     ["| " + " | ".join(fmt(c) for c in row) + " |" for row in body])


def markdown(s: dict) -> str:
    parts = ["# Step-7 results", "", f"Generated {s['generated_at']} by `python -m evals.runners.report` from "
             "`evals/results/`. Sample sizes are what the free Groq tier allowed; see docs/EVALUATION.md.", ""]
    d = s.get("decisions") or {}
    if d:
        parts += ["## Decisions (dev set, shipped graph up to pricing)", "", table(
            ["Jobs", "Bid-or-skip F1", "Precision", "Recall", "Accuracy", "Scam P", "Scam R", "Price in fair band",
             "p50 / p95 s", "Tokens / job", "USD / job"],
            [[d["n_jobs"], d["bid_or_skip"]["f1"], d["bid_or_skip"]["precision"], d["bid_or_skip"]["recall"],
              d["bid_or_skip"]["accuracy"], d["scam"]["precision"], d["scam"]["recall"],
              d["price_in_fair_band"]["rate"], f"{d['latency_s']['p50']} / {d['latency_s']['p95']}",
              d["tokens_per_job"], d["usd_per_job"]]]), "",
              f"Latency excludes rate-limit pauses: {d['latency_s']['n']} of {d['n_jobs']} runs; the others paused "
              "on the daily token limit before pauses were recorded.", "",
              table(["Job type", "n", "Accuracy"], [[k, v["n"], v["accuracy"]] for k, v in d["by_kind"].items()]), ""]
    v2 = s.get("decisions_v2") or {}
    if v2:
        m = v2["all_jobs_with_v2"]
        parts += ["## Decisions after the injection change (regex v2 + risk prompt v2)", "",
                  f"{v2['rerun_jobs']} jobs re-run; correct {v2['rerun_correct_v1']} -> {v2['rerun_correct_v2']}; "
                  f"scams bid on {v2['scams_bid_on_v2']} of {v2['scams_in_rerun']}. All {m['n_jobs']} jobs with the "
                  "re-run rows substituted:", "", table(
                      ["Jobs", "Bid-or-skip F1", "Precision", "Recall", "Accuracy", "Scam P", "Scam R"],
                      [[m["n_jobs"], m["bid_or_skip"]["f1"], m["bid_or_skip"]["precision"], m["bid_or_skip"]["recall"],
                        m["bid_or_skip"]["accuracy"], m["scam"]["precision"], m["scam"]["recall"]]]), "",
                  table(["Job", "Kind", "Gold", "v1", "v2", "Risk v1 -> v2"],
                        [[c["job_id"], c["kind"], c["gold"], c["v1"], c["v2"], f"{c['v1_risk']} -> {c['v2_risk']}"]
                         for c in v2["changes"]]), ""]
    b = s.get("proposal_quality") or {}
    if b:
        parts += ["## R&D question B: V0 vs the shipped retriever", "", table(
            ["Variant", "Recall@5", "nDCG@10", "Unsupported claims / proposal (first draft)",
             "Unsupported after verification (yardstick)", "Judge score", "GigSim reply rate", "p95 latency (s)",
             "Cost / proposal (USD)"],
            [[k, v["recall@5"], v["ndcg@10"], v["unsupported_per_proposal_before_verification"],
              v["unsupported_per_proposal_after_verification_yardstick"], v["judge_score"], v["gigsim_reply_rate"],
              v["p95_latency_s"], v["cost_per_proposal_usd"]] for k, v in b.items()]), ""]
    c = s.get("orchestration") or {}
    if c:
        parts += ["## R&D question C: single agent vs supervisor vs graph", "", table(
            ["System", "Jobs", "Failed runs", "Bid-or-skip accuracy", "Scams bid on", "Unsupported claims / proposal",
             "Violations left in drafts", "Caught by Compliance", "Injections followed", "GigSim reply rate",
             "Tokens / job", "p95 latency (s)"],
            [[v["system"], v["n_jobs"], v["failed_runs"], v["bid_or_skip_accuracy"], v["scams_bid_on"],
              v["unsupported_claims_per_proposal"], v["drafts_with_policy_violations_left"],
              v["violations_caught_by_compliance"], v["injections_followed"], v["gigsim_reply_rate"],
              v["tokens_per_job"], v["latency_p95_s"]] for v in c.values()]), ""]
    inj = s.get("injection") or {}
    if inj:
        parts += ["## Injection attack success rate", "", table(
            ["Layer", "Defences", "Attack success rate"],
            [[k, inj["layers"][k], inj[f"asr_{k}"]] for k in ("L0", "L1", "L2", "L3")]),
            "", f"n = {inj['n']} planted-injection jobs; writer {inj['writer_model']}; detected before drafting: "
                f"{inj['detected_rate']}.", ""]
    a = (s.get("tool_granularity") or {}).get("sets") or {}
    if a:
        parts += ["## R&D question A: tool granularity", "", table(
            ["Tool set", "Tasks", "Tool selection accuracy", "Argument accuracy", "Fully correct calls",
             "Tokens / task"],
            [[k, v["n_tasks"], v["tool_selection_accuracy"], v["argument_accuracy"], v["fully_correct_calls"],
              v["tokens_per_task"]] for k, v in a.items()]), ""]
    jc = s.get("judge_calibration") or {}
    if jc:
        blocks = [("v1 (judge.md)", jc)] + ([("v2 (judge_v2.md)", jc["judge_v2"])] if "judge_v2" in jc else [])
        parts += ["## Judge calibration", "", f"{jc['n']} items labelled by hand; judge {jc['judge_model']}. "
                  "v2 was written after seeing v1's disagreements on these items, so its agreement is optimistic.", "",
                  table(["Prompt", "Kappa, score (quadratic)", "Kappa, send", "Exact", "Within one", "Mean judge",
                         "Mean human", "Send: judge / human", "Kappa, injection items", "Kappa, other items"],
                        [[k, b["kappa_score_quadratic"], b["kappa_send"], b["exact_agreement"], b["within_one"],
                          b["mean_judge"], b["mean_human"], f"{b['send_judge']} / {b['send_human']}",
                          b["injection_items"]["kappa_score_quadratic"], b["other_items"]["kappa_score_quadratic"]]
                         for k, b in blocks]), ""]
    x = s.get("x2") or {}
    if x:
        parts += ["## Extension X2: pricing A/B in GigSim", "", table(
            ["Arm", "Submitted", "Reply rate", "Interview rate", "Hire rate"],
            [[k, x[k]["submitted"], x[k]["reply_rate"], x[k]["interview_rate"], x[k]["hire_rate"]]
             for k in ("A_shipped_price", "B_premium_price")]),
            "", f"{x['pairs']} paired jobs; discordant A-only {x['discordant_A_only']}, B-only "
                f"{x['discordant_B_only']}; McNemar exact p = {x['mcnemar_exact_p']}.", ""]
    bu = s.get("budgets")
    if bu:
        dcl = bu["declared"]
        parts += ["## Budgets (E5): declared vs measured", "", table(
            ["Unit", "Declared p95 (s)", "Measured p95 (s)", "Declared USD", "Measured USD", "Runs", "Source"],
            [["Job analysed", dcl["job_analysed_p95_s"], bu["job_analysed"]["p95_s"], dcl["job_analysed_usd"],
              bu["job_analysed"]["usd"], bu["job_analysed"]["n"], bu["job_analysed"]["source"]],
             ["Proposal", dcl["proposal_p95_s"], bu["proposal"]["p95_s"], dcl["proposal_usd"], bu["proposal"]["usd"],
              bu["proposal"]["n"], bu["proposal"]["source"]]]), ""]
    return "\n".join(parts)


def main() -> None:
    os.environ.setdefault("GIGBID_DB", "sqlite")
    s = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "decisions": decisions(),
         "decisions_v2": decisions_v2(), "proposal_quality": proposal_quality(), "orchestration": orchestration(),
         "injection": load("injection_asr.json"), "tool_granularity": load("tool_granularity.json"),
         "judge_calibration": load("judge_calibration.json"), "x2": load("x2_pricing.json"),
         "outcomes": load("outcomes.json")}
    s["budgets"] = budgets(s["decisions"])
    (RESULTS / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    (RESULTS / "RESULTS.md").write_text(markdown(s) + "\n")
    print(markdown(s))


if __name__ == "__main__":
    main()
