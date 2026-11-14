"""Technology vocabulary for claim verification (rag/verify.py), built from real Upwork skill tags.

Why: the 57-skill taxonomy only covers the three personas. A draft that says "built with Kotlin"
must be caught even though Kotlin is not a persona skill, so the verifier needs to recognise
technology names in general.

Source: Category_1..9 of the Upwork 60k dataset (real skill tags clients attached to jobs).
Tags mix technologies ("Kotlin", "Flutter") with generic phrases ("Market Research",
"Communications"). We keep a tag when job descriptions write it in its tagged capitalisation:
technology names are proper nouns ("We use Flutter"), generic phrases mostly appear in
lower case ("some market research"). Rules (all in this file, reproducible):
  - tag used on >= MIN_TAG_COUNT jobs, 3+ characters, at most 3 words;
  - appears >= MIN_MENTIONS times in descriptions (case-insensitive, whole word);
  - written exactly as tagged in >= MIN_CASE_RATIO of those mentions, or contains . + or #
    (Node.js, C++, .NET), which only technologies do;
  - natural languages are dropped ("English" is capitalised but not a technology).

Output: rag/tech_terms.json (committed; data/raw is not).
    python data/pipeline/clean/tech_terms.py
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "data/raw/upwork_freelance_jobs_60k/Final_Upwork_Dataset.csv"
OUT = ROOT / "rag/tech_terms.json"
MIN_TAG_COUNT, MIN_MENTIONS, MIN_CASE_RATIO = 20, 10, 0.7
LANGUAGES = {"English", "Spanish", "French", "German", "Arabic", "Chinese", "Japanese", "Portuguese", "Italian",
             "Russian", "Korean", "Dutch", "Hindi", "Urdu", "Turkish", "Polish", "Swedish", "Hebrew", "Vietnamese",
             "Indonesian", "Thai", "Greek", "Romanian", "Ukrainian", "Czech", "Danish", "Norwegian", "Finnish",
             "Mandarin Chinese", "Cantonese", "Tagalog", "Filipino", "Bengali", "Persian", "Malay"}


def build() -> dict:
    cols = [f"Category_{i}" for i in range(1, 10)]
    df = pd.read_csv(SRC, usecols=cols + ["Description"])
    tags = collections.Counter(str(v).strip() for c in cols for v in df[c].dropna() if str(v).strip())
    cand = [t for t, n in tags.items() if n >= MIN_TAG_COUNT and len(t) >= 3 and len(t.split()) <= 3
            and t not in LANGUAGES]
    by_low = {t.lower(): t for t in cand}
    rx = re.compile(r"(?<![\w.])(" + "|".join(re.escape(t) for t in sorted(by_low, key=len, reverse=True))
                    + r")(?!\w)", re.I)
    total, exact = collections.Counter(), collections.Counter()
    for text in df["Description"].dropna().astype(str):
        for m in rx.finditer(text):
            t = by_low[m.group(1).lower()]
            total[t] += 1
            exact[t] += m.group(1) == t
    keep = sorted(t for t in cand if total[t] >= MIN_MENTIONS and
                  (exact[t] / total[t] >= MIN_CASE_RATIO or re.search(r"[.+#]", t)))
    return {"source": "Upwork 60k (ahmedmyalo/upwork-freelance-jobs-60k) Category_1..9 skill tags",
            "rules": {"min_tag_count": MIN_TAG_COUNT, "min_mentions": MIN_MENTIONS, "min_case_ratio": MIN_CASE_RATIO},
            "n_tags_seen": len(tags), "n_candidates": len(cand), "terms": keep}


if __name__ == "__main__":
    out = build()
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(out['terms'])} terms of {out['n_candidates']} candidates -> {OUT.relative_to(ROOT)}")
