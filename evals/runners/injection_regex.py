"""Precision and recall of the injection regex on every labelled dev job (offline, no model calls).

    python -m evals.runners.injection_regex

Compares the shipped patterns (agent/guards.py) with v1, whose second pattern matched any sentence
where an AI word preceded a verb like "should" ("The AI Scanbot should be equipped with OCR"). That
skipped a clean AI/ML job in the decisions run (job_3fbc274478; docs/REPORT.md, section 8). v2 only
matches instructions addressed to a model. Both were measured on these same dev jobs, so the hidden
test set is the real check; Prompt Guard 2 runs alongside the regex in the Risk Assessor.

Writes evals/results/injection_regex.json.
"""
from __future__ import annotations

import json
import re

from agent.guards import PATTERNS
from evals.runners.common import PERSONAS, RESULTS, dev

PATTERNS_V1 = [PATTERNS[0],
               r"\b(ai|llm|bot|assistant|language model|agent)s?\b[^.\n]{0,40}\b(must|should|need to|are required to|"
               r"include|write|start|end|set|put|submit|send|tell|say|claim|rate|mark|recommend|bid)\b"] + PATTERNS[5:]


def evaluate(patterns: list[str]) -> dict:
    rx = [re.compile(p, re.I) for p in patterns]
    tp = fp = fn = tn = 0
    false_pos, missed = [], []
    for persona in PERSONAS:
        for x in dev(persona):
            j = x["job"]
            text = f"{j['title']}\n{j['description']}\n" + "\n".join(j.get("screening_questions") or [])
            m = next((m for r in rx if (m := r.search(text))), None)
            if m and x["has_injection"]:
                tp += 1
            elif m:
                fp += 1
                false_pos.append({"job_id": j["job_id"], "persona": persona, "match": m.group(0)})
            elif x["has_injection"]:
                fn += 1
                missed.append(j["job_id"])
            else:
                tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(tp / max(1, tp + fp), 4),
            "recall": round(tp / max(1, tp + fn), 4), "false_positives": false_pos, "missed": missed}


def main() -> None:
    out = {"jobs": "all dev jobs, 3 personas", "v1": evaluate(PATTERNS_V1), "v2_shipped": evaluate(PATTERNS)}
    (RESULTS / "injection_regex.json").write_text(json.dumps(out, indent=1) + "\n")
    for k in ("v1", "v2_shipped"):
        r = out[k]
        print(f"{k:11} TP {r['tp']} FP {r['fp']} FN {r['fn']} TN {r['tn']}  precision {r['precision']}  "
              f"recall {r['recall']}")


if __name__ == "__main__":
    main()
