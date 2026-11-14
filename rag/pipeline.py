"""Composable retrieval pipeline. Each RAG variant is a configuration of it.

    query      "post"      one query: the whole job post                         (V0)
               "decompose" one query per extracted requirement, fused with RRF   (V1)
    retrieval  "dense" | "bm25" | "hybrid" (BM25 + dense, reciprocal rank fusion) (V2)
    rerank     cross-encoder over the top candidates of each query             (V3)
    outcome    past proposals become a signal, not evidence: their outcome
               boosts the projects they cited, and they leave the result list   (V4)
    multivector rank projects by their best document (summary, README or code),
               then return each project's documents together                    (V5)
    corrective grade the best evidence; re-query on the job's skills if weak;
               return nothing when there is still no support                     (V8)

Because every variant shares this code, the ablation (best pipeline minus one
component) is just a config change, and differences between variants come from
the component, not from incidental code differences.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from rag.index.bm25 import BM25
from rag.index.dense import DenseIndex
from rag.ingest.corpus import Doc
from rag.models import embed_queries, rag_config, rerank_scores, sigmoid
from rag.query import post_text, requirements

TYPE_ORDER = {"case_study": 0, "code": 1, "readme": 2, "review": 3, "cv": 4, "proposal": 5}
OUTCOME_WEIGHT = {"won": 1.0, "replied": 0.6, "ignored": 0.1}


@dataclass(frozen=True)
class Config:
    name: str
    query: Literal["post", "decompose"] = "post"
    retrieval: Literal["dense", "bm25", "hybrid"] = "dense"
    rerank: bool = False
    outcome: bool = False
    multivector: bool = False
    corrective: bool = False


@dataclass
class Result:
    evidence_ids: list[str]
    scores: list[float]
    queries: list[str]
    refused: bool = False
    grade: float | None = None             # best cross-encoder grade (corrective / rerank)
    requeried: bool = False
    notes: dict = field(default_factory=dict)


class Pipeline:
    """`pool="portfolio"` (default) returns only portfolio evidence (CV, case studies, READMEs,
    code, reviews); past proposals are still read as a signal by the outcome component.
    `pool="all"` also returns past proposals as results (the naive setting: see the
    finding in docs/RAG.md that proposals then take most of the top-5 slots)."""

    def __init__(self, docs: list[Doc], cfg: Config, pool: Literal["portfolio", "all"] = "portfolio"):
        self.docs, self.cfg, self.pool = docs, cfg, pool
        self.rc = rag_config()
        self.dense = DenseIndex(docs, size=self.rc["chunk_words"], overlap=self.rc["chunk_overlap"]) \
            if cfg.retrieval in ("dense", "hybrid") else None
        self.bm25 = BM25([f"{d.title}\n{d.text}" for d in docs]) if cfg.retrieval in ("bm25", "hybrid") else None
        self.is_prop = np.array([d.type == "proposal" for d in docs])
        # Candidates: proposals are needed as candidates whenever the outcome component is on
        # (it transfers their relevance to the projects they cited, then drops them). `pool`
        # only decides whether proposals may be *returned*.
        self.cand_props = pool == "all" or cfg.outcome
        self.allowed = [i for i, d in enumerate(docs) if self.cand_props or d.type != "proposal"]
        self.project_prior = self._project_prior() if cfg.outcome else {}

    # ---------------------------------------------------------------- components
    def _project_prior(self) -> dict[str, float]:
        """Smoothed outcome score of the proposals that cited each project."""
        agg: dict[str, list[float]] = {}
        for d in self.docs:
            if d.type == "proposal" and d.project_id:
                agg.setdefault(d.project_id, []).append(OUTCOME_WEIGHT.get(d.outcome or "", 0.1))
        base = np.mean([w for v in agg.values() for w in v]) if agg else 0.3
        return {p: (sum(v) + 2 * base) / (len(v) + 2) for p, v in agg.items()}

    def _candidates(self, q: str, qvec: np.ndarray | None, k: int) -> tuple[list[int], dict[int, str]]:
        """Top-k doc indices for one query, plus the passage that matched (for reranking)."""
        passages: dict[int, str] = {}
        if self.cfg.retrieval == "bm25":
            ranked = [i for i, _ in self.bm25.top(q, k, self.allowed)]
        else:
            ds, chunk = self.dense.doc_scores(qvec)
            if not self.cand_props:
                ds = np.where(self.is_prop, -2.0, ds)
            dense_rank = list(np.argsort(-ds)[:k * 2])
            for i in dense_rank:
                passages[int(i)] = self.dense.chunks[chunk[i]]
            if self.cfg.retrieval == "dense":
                ranked = [int(i) for i in dense_rank[:k]]
            else:
                bm = [i for i, _ in self.bm25.top(q, k * 2, self.allowed)]
                ranked = rrf([[int(i) for i in dense_rank], bm], self.rc["rrf_k"])[:k]
        for i in ranked:
            passages.setdefault(i, self.docs[i].text[:1200])
        return ranked, passages

    def _rerank(self, q: str, ranked: list[int], passages: dict[int, str]) -> tuple[list[int], dict[int, float]]:
        top = ranked[: self.rc["rerank_top"]]
        sc = rerank_scores(q[:700], [passages[i][:1200] for i in top])   # long pairs make the cross-encoder slow
        order = sorted(range(len(top)), key=lambda j: -sc[j])
        return [top[j] for j in order] + ranked[len(top):], {top[j]: sc[j] for j in range(len(top))}

    # ---------------------------------------------------------------- main
    def search(self, job: dict, k: int = 10, reqs: list[str] | None = None) -> Result:
        queries = (reqs or requirements(job, self.rc["max_requirements"])) if self.cfg.query == "decompose" \
            else [post_text(job)]
        res = self._run(queries, k)
        if self.cfg.corrective:
            res = self._correct(job, queries, res, k)
        return res

    def _run(self, queries: list[str], k: int) -> Result:
        cand_k = self.rc["candidates"]
        qvecs = embed_queries(queries) if self.dense is not None else [None] * len(queries)
        rankings, grades = [], {}
        for q, qv in zip(queries, qvecs):
            ranked, passages = self._candidates(q, qv, cand_k)
            if self.cfg.rerank or self.cfg.corrective:
                reranked, sc = self._rerank(q, ranked, passages)
                if self.cfg.rerank:
                    ranked = reranked
                for i, s in sc.items():
                    grades[i] = max(grades.get(i, -1e9), s)
            rankings.append(ranked)
        fused = rrf(rankings, self.rc["rrf_k"], scores=True) if len(rankings) > 1 else \
            {i: 1.0 / (self.rc["rrf_k"] + r + 1) for r, i in enumerate(rankings[0])}

        if self.cfg.outcome:
            for i in list(fused):
                d = self.docs[i]
                if d.type == "proposal":
                    if d.project_id:     # transfer the proposal's relevance to the project it cited
                        for j, dj in enumerate(self.docs):
                            if dj.project_id == d.project_id and dj.type != "proposal":
                                fused[j] = fused.get(j, 0.0) + 0.5 * fused[i] * OUTCOME_WEIGHT.get(d.outcome or "", 0.1)
                    del fused[i]
            for i in list(fused):
                p = self.docs[i].project_id
                if p in self.project_prior:
                    fused[i] *= 1 + self.project_prior[p]

        order = sorted(fused, key=lambda i: -fused[i])
        if self.cfg.multivector:
            order = self._group_by_project(order, fused)
        top = order[:k]
        best = max((grades[i] for i in top[:5] if i in grades), default=None)
        return Result([self.docs[i].evidence_id for i in top], [round(fused[i], 6) for i in top], queries,
                      grade=None if best is None else sigmoid(best))

    def _group_by_project(self, order: list[int], score: dict[int, float]) -> list[int]:
        proj_score: dict[str, float] = {}
        for i in order:
            p = self.docs[i].project_id
            if p and self.docs[i].type != "proposal":
                proj_score[p] = max(proj_score.get(p, 0.0), score[i])
        members: dict[str, list[int]] = {}
        for j, d in enumerate(self.docs):
            if d.project_id in proj_score and d.type != "proposal":
                members.setdefault(d.project_id, []).append(j)
        units = [(s, "p", p) for p, s in proj_score.items()]
        units += [(score[i], "d", i) for i in order if not self.docs[i].project_id or self.docs[i].type == "proposal"]
        out: list[int] = []
        for s, kind, x in sorted(units, key=lambda u: -u[0]):
            if kind == "p":
                for j in members[x]:            # documents not retrieved themselves inherit the project score
                    score.setdefault(j, s * 0.999)
                out += sorted(members[x], key=lambda j: (TYPE_ORDER[self.docs[j].type], -score.get(j, 0)))
            else:
                out.append(x)
        return out

    def _correct(self, job: dict, queries: list[str], res: Result, k: int) -> Result:
        lo, refuse = self.rc["corrective_threshold"], self.rc["corrective_refuse"]
        if res.grade is not None and res.grade >= lo:
            return res
        skills = job.get("skills") or []
        retry = [f"{job['title']}. Skills: {', '.join(skills)}"] + [f"experience with {s}" for s in skills[:4]]
        res2 = self._run(retry, k)
        res2.requeried = True
        res2.queries = queries + retry
        if res2.grade is None or res2.grade < refuse:
            return Result([], [], res2.queries, refused=True, grade=res2.grade, requeried=True)
        return res2 if (res2.grade or 0) >= (res.grade or 0) else res


def rrf(rankings: list[list[int]], k: int = 60, scores: bool = False):
    s: dict[int, float] = {}
    for r in rankings:
        for pos, i in enumerate(r):
            s[i] = s.get(i, 0.0) + 1.0 / (k + pos + 1)
    return s if scores else sorted(s, key=lambda i: -s[i])
