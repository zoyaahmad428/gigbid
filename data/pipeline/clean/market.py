"""Market price statistics per category, from real data only.

  upwork_all_jobs_200k      posted budgets (fixed) and hourly ranges, Feb-Jul 2024
  freelance_contracts_1_3m  hourly rates and totals actually paid on contracts

Both datasets have titles only, so the category comes from skills in the title.
Output feeds the Pricing Strategist (market stats tool) and the dev-set fair bands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taxonomy import categorise, skills_in_text  # noqa: E402

SLUG_200K = "asaniczka/all-jobs-on-upwork-200k-plus"
SLUG_CONTRACTS = "asaniczka/freelance-contracts-dataset-1-3-million-entries"
PCTS = [10, 25, 50, 75, 90]


def _title_category(titles: pd.Series) -> pd.Series:
    cache: dict[str, str] = {}

    def f(t: str) -> str:
        t = str(t)
        if t not in cache:
            s = skills_in_text(t)
            cache[t] = categorise(s, s)[0]
        return cache[t]
    return titles.map(f)


def _pct(x: pd.Series) -> dict:
    x = x.dropna()
    if len(x) < 20:
        return {"n": int(len(x))}
    return {"n": int(len(x)), **{f"p{p}": round(float(np.percentile(x, p)), 2) for p in PCTS}}


def market_stats(raw: Path) -> tuple[dict, dict]:
    files = sorted((raw / "upwork_all_jobs_200k").glob("*.csv"))
    jobs = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    jobs = jobs.drop_duplicates("link")
    jobs["category"] = _title_category(jobs["title"])

    con = pd.read_csv(raw / "freelance_contracts_1_3m" / "contracts.csv",
                      usecols=["job_title", "start_date", "total_hours", "total_paid", "hourly_rate"])
    con = con[pd.to_datetime(con["start_date"], errors="coerce").dt.year.between(2015, 2024)]
    con["category"] = _title_category(con["job_title"])

    out: dict = {}
    for cat in sorted(set(jobs["category"]) | set(con["category"])):
        j = jobs[jobs["category"] == cat]
        c = con[con["category"] == cat]
        fixed = j.loc[j["is_hourly"] == False, "budget"]  # noqa: E712
        fixed = fixed[(fixed >= 5) & (fixed <= 250_000)]
        hl = j.loc[j["is_hourly"] == True, ["hourly_low", "hourly_high"]]  # noqa: E712
        mid = hl.mean(axis=1)
        mid = mid[(mid >= 3) & (mid <= 500)]
        rate = c["hourly_rate"]
        rate = rate[(rate >= 3) & (rate <= 500)]
        paid = c.loc[c["total_paid"] > 0, "total_paid"]
        out[cat] = {
            "posted_fixed_budget_usd": _pct(fixed),
            "posted_hourly_mid_usd": _pct(mid),
            "contract_hourly_rate_usd": _pct(rate),
            "contract_total_paid_usd": _pct(paid),
            "hourly_share_of_postings": round(float((j["is_hourly"] == True).mean()), 3) if len(j) else None,  # noqa: E712
        }
    stats = {"jobs_rows": int(len(jobs)), "contract_rows_2015_2024": int(len(con)),
             "jobs_by_category": jobs["category"].value_counts().to_dict(),
             "contracts_by_category": con["category"].value_counts().to_dict()}
    return out, stats


def contracts_sample(raw: Path, n: int, seed: int) -> pd.DataFrame:
    """Real completed contracts, used as past-hire history for synthetic client ids."""
    con = pd.read_csv(raw / "freelance_contracts_1_3m" / "contracts.csv")
    con = con[(con["total_paid"] > 0) & con["end_date"].notna()]
    d = pd.to_datetime(con["end_date"], errors="coerce")
    con = con[d.dt.year.between(2018, 2024)]
    return con.sample(n=min(n, len(con)), random_state=seed).reset_index(drop=True)
