"""Scout: run a saved search, drop jobs already seen and near-duplicates, queue the rest.

No model: dedup is exact (normalised text hash) plus near-duplicate detection
with word-shingle Jaccard similarity against recently seen jobs, which catches
re-posts with a new title suffix, swapped phrases or reordered paragraphs.
"""
from __future__ import annotations

import hashlib
import re
import time

from agent.memory.store import get_store
from agent.nodes.base import tools
from agent.settings import config


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def shingles(text: str, k: int = 4) -> set[str]:
    w = _norm(text).split()
    return {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


async def scout(user_id: str, search: dict, *, max_pages: int = 3) -> dict:
    """Returns {"new": [job...], "duplicates": [{job_id, dup_of, similarity}], "seen": n}."""
    store = get_store()
    ns = ("scout_seen", user_id)
    recent = [i.value for i in store.search(ns, limit=2000)]
    th = config()["thresholds"]["duplicate_jaccard"]
    new, dups, seen = [], [], 0
    cursor = None
    for _ in range(max_pages):
        page = await tools().call("scout", "search_jobs", {**search, "limit": 50, **({"cursor": cursor} if cursor else {})})
        for j in page["jobs"]:
            if store.get(ns, j["job_id"]):
                seen += 1
                continue
            text = f"{j['title']} {j['description']}"
            fp = hashlib.sha256(_norm(j["description"]).encode()).hexdigest()[:16]
            sh = shingles(text)
            dup = next((r for r in recent if r["fp"] == fp), None)
            sim = 1.0 if dup else 0.0
            if not dup:
                best = max(recent, key=lambda r: jaccard(sh, set(r["sh"])), default=None)
                if best and (s := jaccard(sh, set(best["sh"]))) >= th:
                    dup, sim = best, s
            rec = {"job_id": j["job_id"], "fp": fp, "sh": sorted(sh)[:400], "ts": time.time(),
                   "dup_of": dup["job_id"] if dup else None}
            store.put(ns, j["job_id"], rec)
            recent.append(rec)
            if dup:
                dups.append({"job_id": j["job_id"], "dup_of": dup["job_id"], "similarity": round(sim, 3)})
            else:
                new.append(j)
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return {"new": new, "duplicates": dups, "seen": seen}
