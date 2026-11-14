"""30-job smoke evaluation with thresholds (E2): fails if bid-or-skip F1 drops or unsupported claims rise.

    python -m evals.runners.graph_runs --set smoke --stop critic --tag smoke    # needs the LLM key
    python -m evals.runners.smoke_gate                                          # compare with the thresholds

The smoke set is the first 30 jobs of the decisions set (bids, injected bids, no-fit and budget
skips and scams for all three personas). Each job runs the shipped graph to the first verified draft.
Thresholds live in evals/baselines/smoke_gate.json; CI runs this on pull requests when the
repository has a GROQ_API_KEY secret (on the free tier one run uses about 150k tokens).
"""
from __future__ import annotations

import json
import os
import sys

from evals.runners.common import RESULTS, ROOT
from evals.runners.report import prf, rows

THRESHOLDS = ROOT / "evals/baselines/smoke_gate.json"


def main() -> None:
    th = json.loads(THRESHOLDS.read_text())
    rs = [r for r in rows(os.environ.get("SMOKE_TAG", "smoke")) if not r.get("error")]
    if len(rs) < th["min_jobs"]:
        print(f"only {len(rs)} smoke jobs finished (need {th['min_jobs']})", file=sys.stderr)
        sys.exit(1)
    bid = prf([r["decision"] == "bid" for r in rs], [r["gold_action"] == "bid" for r in rs])
    first = [r.get("verification_first") or {} for r in rs if r.get("first_draft")]
    claims = sum(f.get("claims", 0) for f in first)
    rate = sum(f.get("unsupported", 0) for f in first) / claims if claims else 0.0
    res = {"jobs": len(rs), "bid_or_skip_f1": bid["f1"], "unsupported_rate_first_draft": round(rate, 4),
           "thresholds": th}
    (RESULTS / "smoke_gate.json").write_text(json.dumps(res, indent=1) + "\n")
    fails = []
    if bid["f1"] < th["min_bid_or_skip_f1"]:
        fails.append(f"bid-or-skip F1 {bid['f1']} < {th['min_bid_or_skip_f1']}")
    if rate > th["max_unsupported_rate_first_draft"]:
        fails.append(f"unsupported-claim rate {rate:.3f} > {th['max_unsupported_rate_first_draft']}")
    report = (f"### Smoke evaluation ({len(rs)} jobs)\n\n| Metric | Now | Threshold |\n|---|---|---|\n"
              f"| Bid-or-skip F1 | {bid['f1']} | ≥ {th['min_bid_or_skip_f1']} |\n"
              f"| Unsupported-claim rate (first drafts) | {rate:.3f} | ≤ {th['max_unsupported_rate_first_draft']} |\n")
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(report)
    if fails:
        print("SMOKE GATE FAILED: " + "; ".join(fails), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
