"""Okapi BM25 over in-memory documents (no external dependencies).

Used by the Portfolio server's default search and as the lexical half of the
hybrid variant (V2). Tokens keep dots, pluses and hashes so "next.js", "c++"
and "c#" survive, and code identifiers are split on underscores.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]")
STOP = frozenset("a an the and or of to in for on with at by from is are be as we you your our this that it i "
                 "my me was were will can have has had not but if then so do does did into over about".split())


def tokenize(text: str) -> list[str]:
    toks = []
    for t in _TOKEN.findall(text.lower().replace("_", " ")):
        t = t.rstrip(".")
        if t and t not in STOP:
            toks.append(t)
    return toks


class BM25:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(t)) for t in texts]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(1, len(self.lens))
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for d, ln in zip(self.docs, self.lens):
            s = 0.0
            for t in q:
                tf = d.get(t)
                if tf:
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * ln / self.avg))
            out.append(s)
        return out

    def top(self, query: str, k: int, allowed: list[int] | None = None) -> list[tuple[int, float]]:
        sc = self.scores(query)
        idx = allowed if allowed is not None else range(len(sc))
        return sorted(((i, sc[i]) for i in idx if sc[i] > 0), key=lambda x: -x[1])[:k]
