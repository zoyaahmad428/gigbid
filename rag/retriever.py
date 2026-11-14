"""Retriever factory: the variant used by the Portfolio server is chosen by config.

Every retriever is built over one persona's docs and implements
    search(query: str, k: int, allowed: list[int] | None) -> list[tuple[int, float]]
returning (doc index, score), best first. Variants V0-V9 register here in step 3;
the server only knows the name in PORTFOLIO_RETRIEVER (default "bm25").
"""
from __future__ import annotations

import os
from typing import Callable, Protocol

from rag.index.bm25 import BM25
from rag.ingest.corpus import Doc


class Retriever(Protocol):
    def search(self, query: str, k: int, allowed: list[int] | None = None) -> list[tuple[int, float]]: ...


class BM25Retriever:
    def __init__(self, docs: list[Doc]):
        self.index = BM25([f"{d.title}\n{d.text}" for d in docs])

    def search(self, query: str, k: int, allowed: list[int] | None = None) -> list[tuple[int, float]]:
        return self.index.top(query, k, allowed)


class PipelineRetriever:
    """Adapts a job-level pipeline variant to the requirement-level search the Portfolio
    server needs: the requirement becomes a one-line job post."""

    def __init__(self, docs: list[Doc], cfg):
        from rag.pipeline import Pipeline
        self.docs, self.pipe = docs, Pipeline(docs, cfg)
        self.pos = {d.evidence_id: i for i, d in enumerate(docs)}
        self.fallback = BM25Retriever(docs)

    def search(self, query: str, k: int, allowed: list[int] | None = None) -> list[tuple[int, float]]:
        """Pipeline results first, filtered to `allowed`. When a filter leaves fewer than k (for
        example types=["proposal"]: the outcome component reads proposals as a signal and never
        returns them), the rest is filled from a lexical search over the allowed documents, with
        scores scaled below the pipeline's so the order stays pipeline-first."""
        res = self.pipe.search({"title": query, "description": query, "skills": []}, k=max(k * 4, 20))
        ok = set(allowed) if allowed is not None else None
        out = [(self.pos[e], s) for e, s in zip(res.evidence_ids, res.scores) if ok is None or self.pos[e] in ok][:k]
        if len(out) < k:
            seen, floor = {i for i, _ in out}, min((s for _, s in out), default=1.0)
            extra = [(i, s) for i, s in self.fallback.search(query, k * 2, allowed) if i not in seen]
            top = max((s for _, s in extra), default=1.0) or 1.0
            out += [(i, floor * 0.99 * s / top) for i, s in extra][: k - len(out)]
        return out


def _variants() -> dict[str, Callable[[list[Doc]], Retriever]]:
    import importlib
    import pkgutil

    import rag.variants as pkg
    out: dict[str, Callable[[list[Doc]], Retriever]] = {}
    for m in pkgutil.iter_modules(pkg.__path__):
        cfg = getattr(importlib.import_module(f"rag.variants.{m.name}"), "CONFIG", None)
        if cfg is not None:
            out[cfg.name] = lambda docs, c=cfg: PipelineRetriever(docs, c)
    return out


REGISTRY: dict[str, Callable[[list[Doc]], Retriever]] = {"bm25": BM25Retriever, **_variants()}


def build(docs: list[Doc], name: str | None = None) -> Retriever:
    name = name or os.environ.get("PORTFOLIO_RETRIEVER", "bm25")
    if name not in REGISTRY:
        raise ValueError(f"unknown retriever {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](docs)
