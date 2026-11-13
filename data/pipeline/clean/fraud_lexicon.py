"""Scam lure phrases learned from labelled fraudulent job ads (trial_1).

trial_1's licence is "unknown" on Kaggle, so none of its text is redistributed.
We keep only aggregate statistics: the word n-grams most over-represented in
fraudulent vs genuine ads (smoothed log-odds), each with its counts. The scam
generator samples lure phrases from this list, and the Risk Assessor can use it
as a feature list.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import pandas as pd

SLUG = "adityammantri/trial-1"
STOP = set("a an the and or of to in for on with at by from is are be as we you your our this that it "
           "will can all us have has who what job work team".split())
_TOKEN = re.compile(r"[a-z]+")


def _ngrams(text: str, n: int) -> set[str]:
    toks = _TOKEN.findall(str(text).lower())
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def fraud_lexicon(raw: Path, top: int = 60, min_count: int = 40) -> tuple[list[dict], dict]:
    df = pd.read_excel(raw / "trial_1" / "processed_labeled_dataset_without_encoding.xlsx",
                       usecols=["job_title", "job_desc", "skills_desc", "fraudulent"])
    df = df.dropna(subset=["fraudulent"])
    df["text"] = df[["job_title", "job_desc", "skills_desc"]].fillna("").agg(" ".join, axis=1)
    df = df.drop_duplicates("text")                 # 48% of descriptions repeat
    # ~10k fraudulent rows share one machine-generated template ("earn N/week immediate
    # hiring contact now at <email> ... no degree required flexible hours"). They would
    # dominate every statistic, so they are counted and excluded.
    templ = df["text"].str.contains("immediate hiring contact now", regex=False)
    n_template = int(templ.sum())
    df = df[~templ]
    # Company boilerplate repeated across many ads of one employer also skews counts:
    # keep one ad per (first 120 chars of description).
    df = df[~df["job_desc"].fillna("").str[:120].duplicated()]
    fraud, real = Counter(), Counter()
    for text, y in zip(df["text"], df["fraudulent"]):
        grams = _ngrams(text, 2) | _ngrams(text, 3)
        (fraud if y == 1 else real).update(grams)
    nf, nr = int((df["fraudulent"] == 1).sum()), int((df["fraudulent"] == 0).sum())
    rows = []
    for g, cf in fraud.items():
        if cf < min_count:
            continue
        words = g.split()
        if all(w in STOP for w in words) or words[0] in STOP or words[-1] in STOP:
            continue
        if any(len(w) < 3 or w.endswith("com") for w in words):   # hex junk, mangled emails
            continue
        cr = real.get(g, 0)
        lo = math.log((cf + 1) / (nf - cf + 1)) - math.log((cr + 1) / (nr - cr + 1))
        rows.append({"phrase": g, "log_odds": round(lo, 3), "fraud_docs": cf, "genuine_docs": cr})
    rows.sort(key=lambda r: r["log_odds"], reverse=True)
    stats = {"templated_fraud_rows_excluded": n_template, "rows_used": int(len(df)),
             "fraudulent": nf, "genuine": nr}
    return rows[:top], stats
