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


REGISTRY: dict[str, Callable[[list[Doc]], Retriever]] = {"bm25": BM25Retriever}


def build(docs: list[Doc], name: str | None = None) -> Retriever:
    name = name or os.environ.get("PORTFOLIO_RETRIEVER", "bm25")
    if name not in REGISTRY:
        raise ValueError(f"unknown retriever {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](docs)
