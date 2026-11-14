"""Validate the built dataset. Returns a list of problems (empty = OK).

Checks: schema of every record, provenance tags, id uniqueness, label/content
consistency, no leakage of hidden fields, disjoint splits, target sizes, and
that persona texts only state numbers that exist in the project facts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_config, read_jsonl  # noqa: E402
from schema import ClientHidden, ClientPublic, DevItem, EvidenceItem, Job, JobLabel, PastProposal  # noqa: E402

PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
HIDDEN_KEYS = {"must_have_skills", "budget_ceiling_usd", "dislikes_generic_openings", "is_scammer",
               "responsiveness", "prefers_short_proposals"}


def validate(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    P = cfg["paths"]
    gs, pdir, devdir = P["gigsim"], P["personas"], P["dev_set"]
    probs: list[str] = []

    def check(model, rows, name):
        for r in rows:
            try:
                model(**r)
            except Exception as e:  # noqa: BLE001
                probs.append(f"{name}: schema error {str(e)[:160]}")
                return

    hist = read_jsonl(gs / "jobs_historical.jsonl")
    live = read_jsonl(gs / "jobs_live.jsonl")
    labels = read_jsonl(gs / "_hidden" / "live_labels.jsonl")
    pub = read_jsonl(gs / "clients.jsonl")
    hid = read_jsonl(gs / "_hidden" / "clients_hidden.jsonl")
    check(Job, hist, "jobs_historical"); check(Job, live, "jobs_live")
    check(JobLabel, labels, "live_labels"); check(ClientPublic, pub, "clients")
    check(ClientHidden, hid, "clients_hidden")

    # sizes
    if len(hist) != cfg["sizes"]["historical_jobs"]:
        probs.append(f"historical jobs: {len(hist)} != {cfg['sizes']['historical_jobs']}")
    # ids
    for name, rows, key in [("jobs_historical", hist, "job_id"), ("jobs_live", live, "job_id"),
                            ("clients", pub, "client_id")]:
        ids = [r[key] for r in rows]
        if len(ids) != len(set(ids)):
            probs.append(f"{name}: duplicate ids")
    if {r["job_id"] for r in hist} & {r["job_id"] for r in live}:
        probs.append("historical and live share job ids")
    # provenance
    for name, rows in [("jobs_historical", hist), ("jobs_live", live), ("clients", pub), ("clients_hidden", hid)]:
        for r in rows:
            pv = r.get("provenance", {})
            if pv.get("source") not in {"real", "synthetic"}:
                probs.append(f"{name}: record without source tag"); break
            if pv["source"] == "synthetic" and not pv.get("generator"):
                probs.append(f"{name}: synthetic record without generator"); break
    # clients referenced and hidden prefs not leaked
    cids = {c["client_id"] for c in pub}
    if {c["client_id"] for c in hid} != cids:
        probs.append("public and hidden client sets differ")
    for j in hist + live:
        if j["client_id"] not in cids:
            probs.append(f"job {j['job_id']} references unknown client"); break
    for c in pub:
        if HIDDEN_KEYS & set(c):
            probs.append("hidden preference fields present in public clients"); break
    # labels consistent with content
    lab = {l["job_id"]: l for l in labels}
    byid = {j["job_id"]: j for j in live}
    if set(lab) != set(byid):
        probs.append("live labels and live jobs differ")
    for jid, l in lab.items():
        j = byid.get(jid)
        if not j:
            continue
        if l["is_duplicate"]:
            o = byid.get(l["dup_of"])
            if not o:
                probs.append(f"duplicate {jid} points to missing job"); continue
            if l["dup_kind"] == "exact" and (o["description"] != j["description"] or o["title"] != j["title"]):
                probs.append(f"exact duplicate {jid} differs from original")
            if j["posted_at"] <= o["posted_at"]:
                probs.append(f"duplicate {jid} posted before its original")
        if l["has_injection"]:
            src = j["provenance"].get("derived_from")
            if j["provenance"]["source"] != "synthetic" or not src:
                probs.append(f"injected job {jid} not tagged synthetic/derived")
        if l["is_scam"] and j["provenance"]["source"] != "synthetic":
            probs.append(f"scam {jid} not tagged synthetic")
    # PII: real text fields must be masked (synthetic scams use reserved example contacts)
    email = re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.com|company\.test)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    for j in hist + live:
        if j["provenance"]["source"] == "real" and email.search(j["description"]):
            probs.append(f"unmasked email in {j['job_id']}"); break

    # personas
    for p in PERSONAS:
        d = pdir / p
        ev = read_jsonl(d / "evidence_index.jsonl")
        check(EvidenceItem, ev, f"{p}/evidence_index")
        props = read_jsonl(d / "proposals.jsonl")
        check(PastProposal, [{k: v for k, v in x.items() if k != "fit"} for x in props], f"{p}/proposals")
        facts = read_jsonl(d / "facts.jsonl")
        lo, hi = cfg["sizes"]["case_studies_per_persona"]
        if not lo <= len(facts) <= hi:
            probs.append(f"{p}: {len(facts)} projects outside {lo}-{hi}")
        n_prop = cfg["sizes"]["proposals_per_persona"]
        if len(props) != n_prop:
            probs.append(f"{p}: {len(props)} proposals != {n_prop}")
        for e in ev:
            if not e["path"].split("#")[0] or not (d / e["path"].split("#")[0]).exists():
                probs.append(f"{p}: evidence {e['evidence_id']} path missing ({e['path']})"); break
        on_disk = {str(f.relative_to(d)) for f in d.rglob("*") if f.is_file()} - {
            "evidence_index.jsonl", "facts.jsonl", "profile.json", "proposals.jsonl", "reviews.jsonl", "cv.md", "cv.pdf"}
        indexed = {e["path"].split("#")[0] for e in ev}
        if on_disk - indexed:
            probs.append(f"{p}: files not in evidence index (stale?): {sorted(on_disk - indexed)[:3]}")
        hist_ids = {j["job_id"] for j in hist}
        if any(x["job_id"] not in hist_ids for x in props):
            probs.append(f"{p}: proposal for a job not in history")
        # grounding: every number in a case study must appear in that project's facts
        fx = {f["project_id"]: f for f in facts}
        for e in ev:
            if e["type"] != "case_study":
                continue
            f = fx[e["project_id"]]
            text = (d / e["path"]).read_text()
            allowed = set(re.findall(r"\d+(?:\.\d+)?", " ".join(f["metrics"] + [f["start"], f["end"],
                                                                              str(f["weeks"]), f["title"]])))
            body = re.sub(r"```.*?```", "", text, flags=re.S)
            stray = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", body)) - allowed - {"0", "1", "2", "3"}
            if stray:
                probs.append(f"{p}: {e['path']} states numbers not in facts: {sorted(stray)[:5]}")

        dev = read_jsonl(devdir / f"dev_{p}.jsonl")
        check(DevItem, dev, f"dev_{p}")
        if len(dev) != cfg["sizes"]["dev_jobs_per_persona"]:
            probs.append(f"dev_{p}: {len(dev)} items")
        dev_ids = {x["job"]["job_id"] for x in dev}
        if dev_ids & ({j["job_id"] for j in hist} | set(byid)):
            probs.append(f"dev_{p} overlaps historical/live jobs")
        evids = {e["evidence_id"] for e in ev}
        for x in dev:
            if any(e.startswith("prop_") for e in x["relevant_evidence"]):
                probs.append(f"dev_{p}: proposal listed as gold evidence"); break
            if not set(x["relevant_evidence"]) <= {e for e, g in x["evidence_grades"].items() if g == 2}:
                probs.append(f"dev_{p}: gold evidence not graded 2"); break
            if not set(x["relevant_evidence"]) <= evids:
                probs.append(f"dev_{p}: gold evidence id not in index"); break
            if x["gold_action"] == "bid" and not x["relevant_projects"]:
                probs.append(f"dev_{p}: bid item without relevant projects ({x['job']['job_id']})"); break
    return probs


if __name__ == "__main__":
    pr = validate()
    print("OK" if not pr else "\n".join(pr))
    sys.exit(1 if pr else 0)
