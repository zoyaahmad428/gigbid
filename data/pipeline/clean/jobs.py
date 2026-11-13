"""Clean and merge real job postings into one pool.

Sources
  upwork_freelance_jobs_60k  title, description, category tags, budget, experience,
                             duration, Connects cost, client signals (Nov 2023 scrape)
  upwork_jobs_2024_50k       title, description, budget, country, exact timestamp
Both are normalised to the same columns, PII-masked, filtered and de-duplicated.
Output: data/interim/jobs_pool.parquet (gitignored).
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import clean_text, mask_pii  # noqa: E402
from taxonomy import categorise, normalise_skills, skills_in_text  # noqa: E402

SLUG_60K = "ahmedmyalo/upwork-freelance-jobs-60k"
SLUG_50K = "asaniczka/upwork-job-postings-dataset-2024-50k-records"
# The dataset's own documentation says the scrape spans February-March 2023 and gives
# only relative ages ("2 days ago"). The exact scrape time is unknown, so ages are
# anchored to mid-March 2023 and the resulting posted_at is marked approximate.
SCRAPE_DATE_60K = dt.datetime(2023, 3, 15, 12, tzinfo=dt.timezone.utc)

_MONEY_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
_JOINED_RE = re.compile(r"([a-z0-9][.!?:)])([A-Z])")       # "team!We" -> "team! We"
_URL_ID_RE = re.compile(r"~([0-9a-f]{10,})")


def money(s: object) -> float | None:
    if s is None or (isinstance(s, float) and s != s):
        return None
    m = _MONEY_RE.search(str(s))
    if not m:
        return None
    v = float(m.group(0).replace(",", ""))
    return v if v > 0 else None


def url_id(url: str) -> str | None:
    m = _URL_ID_RE.search(str(url).replace("%7E", "~"))
    return m.group(1) if m else None


def ago_to_ts(s: str) -> str:
    m = re.match(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", str(s))
    if not m:
        return SCRAPE_DATE_60K.isoformat()
    n, unit = int(m.group(1)), m.group(2)
    delta = {"minute": dt.timedelta(minutes=n), "hour": dt.timedelta(hours=n),
             "day": dt.timedelta(days=n), "week": dt.timedelta(weeks=n),
             "month": dt.timedelta(days=30 * n)}[unit]
    return (SCRAPE_DATE_60K - delta).isoformat()


def is_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.95


def load_60k(raw: Path) -> pd.DataFrame:
    df = pd.read_csv(raw / "upwork_freelance_jobs_60k" / "Final_Upwork_Dataset.csv")
    cats = [f"Category_{i}" for i in range(1, 10)]
    rows = []
    for d in df.to_dict("records"):
        hourly = d["Payment_type"] == "Hourly"
        lo, hi = (money(d["Start_rate"]), money(d["End_rate"])) if hourly else (None, money(d["Job_Cost"]))
        if hourly and lo is None and hi is None and isinstance(d["Hourly_Rate"], str):
            parts = d["Hourly_Rate"].split("-")
            lo, hi = money(parts[0]), money(parts[-1])
        if not hourly:
            lo = hi
        tags = [d[c] for c in cats if isinstance(d[c], str)]
        rows.append({
            "raw_id": url_id(d["Job_URL"]),
            "source_dataset": SLUG_60K,
            "title": d["Job Title"], "description": d["Description"],
            "tags": tags,
            "budget_type": "hourly" if hourly else "fixed",
            "budget_min": lo, "budget_max": hi,
            "experience_level": d["EX_level_demand"] or None,
            "duration": d["Time_Limitation"] if isinstance(d["Time_Limitation"], str) else None,
            "posted_at": ago_to_ts(d["Posted_from"]),
            "posted_at_approx": True,
            "client_country": d["Client_Country"],
            "connects_cost": int(d["New_Connects_Num"]) if d["New_Connects_Num"] == d["New_Connects_Num"] else None,
            "applicants": d["Applicants_Num"],
            "c_payment_verified": d["Payment_Situation"] == "Payment verified",
            "c_spent": d["Spent($)"], "c_rating": d["Rating"], "c_feedback_num": d["Feedback_Num"],
        })
    return pd.DataFrame(rows)


def load_50k(raw: Path) -> pd.DataFrame:
    df = pd.read_csv(raw / "upwork_jobs_2024_50k" / "upwork-jobs.csv")
    out = pd.DataFrame({
        "raw_id": df["link"].map(url_id),
        "source_dataset": SLUG_50K,
        "title": df["title"], "description": df["description"],
        "tags": [[] for _ in range(len(df))],
        "budget_type": df["is_hourly"].map({True: "hourly", False: "fixed"}),
        "budget_min": df["hourly_low"].where(df["is_hourly"] == True, df["budget"]),  # noqa: E712
        "budget_max": df["hourly_high"].where(df["is_hourly"] == True, df["budget"]),  # noqa: E712
        "experience_level": None, "duration": None,
        "posted_at": pd.to_datetime(df["published_date"], utc=True).map(lambda t: t.isoformat()),
        "posted_at_approx": False,
        "client_country": df["country"], "connects_cost": None, "applicants": None,
        "c_payment_verified": None, "c_spent": None, "c_rating": None, "c_feedback_num": None,
    })
    out = out[out["budget_type"].notna()]            # 16% have no budget information at all
    out.loc[out["budget_min"] <= 0, "budget_min"] = None
    out.loc[out["budget_max"] <= 0, "budget_max"] = None
    return out


def clean_pool(raw: Path) -> tuple[pd.DataFrame, dict]:
    a, b = load_60k(raw), load_50k(raw)
    stats: dict = {"loaded": {SLUG_60K: len(a), SLUG_50K: len(b)}}
    df = pd.concat([a, b], ignore_index=True)

    df["title"] = df["title"].map(clean_text)
    df["description"] = df["description"].map(clean_text).map(lambda t: _JOINED_RE.sub(r"\1 \2", t))

    before = len(df)
    df = df[df["raw_id"].notna()]
    df = df.drop_duplicates("raw_id")                     # same posting in both scrapes
    stats["dropped_duplicate_url"] = before - len(df)

    before = len(df)
    df = df[df["description"].str.len() >= 200]
    stats["dropped_short_description"] = before - len(df)

    before = len(df)
    df = df[(df["title"] + " " + df["description"]).map(is_english)]
    stats["dropped_non_english"] = before - len(df)

    before = len(df)
    key = (df["title"].str.lower().str.replace(r"\W+", " ", regex=True).str.strip() + "|"
           + df["description"].str.lower().str.replace(r"\W+", " ", regex=True).str[:300])
    df = df[~key.duplicated()]                            # re-posts of the same job
    stats["dropped_duplicate_text"] = before - len(df)

    pii = 0
    masked_t, masked_d = [], []
    for t, d in zip(df["title"], df["description"]):
        t2, k1 = mask_pii(t)
        d2, k2 = mask_pii(d)
        masked_t.append(t2); masked_d.append(d2); pii += k1 + k2
    df["title"], df["description"] = masked_t, masked_d
    stats["pii_replacements"] = pii

    skills, cats, fits = [], [], []
    for title, desc, tags in zip(df["title"], df["description"], df["tags"]):
        s = normalise_skills(list(tags))
        for x in skills_in_text(title + "\n" + desc):
            if x not in s:
                s.append(x)
        c, f = categorise(s, skills_in_text(title))
        skills.append(s); cats.append(c); fits.append(f)
    df["skills"], df["category"], df["persona_fit"] = skills, cats, fits

    # Budget sanity: drop impossible values but keep the job (budget becomes unknown).
    hourly = df["budget_type"] == "hourly"
    for col in ("budget_min", "budget_max"):
        df.loc[hourly & ((df[col] < 3) | (df[col] > 500)), col] = None
        df.loc[~hourly & ((df[col] < 5) | (df[col] > 250_000)), col] = None
    swap = df["budget_min"] > df["budget_max"]
    df.loc[swap, ["budget_min", "budget_max"]] = df.loc[swap, ["budget_max", "budget_min"]].values

    df = df.reset_index(drop=True)
    stats["pool_size"] = len(df)
    stats["pool_by_category"] = df["category"].value_counts().to_dict()
    stats["pool_by_source"] = df["source_dataset"].value_counts().to_dict()
    return df, stats
