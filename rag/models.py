"""Open embedding and reranking models (CPU, ONNX via fastembed), loaded once.

Model names come from agent/config.yaml (`models.embeddings`, `models.reranker`)
or the RAG_EMBEDDINGS / RAG_RERANKER environment variables. Each embedding
model gets the query/document prefixes it was trained with.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("RAG_MODEL_CACHE", str(ROOT / "data/processed/models"))
_CFG = yaml.safe_load((ROOT / "agent/config.yaml").read_text())

PREFIXES = {  # (query prefix, document prefix)
    "BAAI/bge-small-en-v1.5": ("Represent this sentence for searching relevant passages: ", ""),
    "BAAI/bge-base-en-v1.5": ("Represent this sentence for searching relevant passages: ", ""),
    "nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
    "nomic-ai/nomic-embed-text-v1.5-Q": ("search_query: ", "search_document: "),
}


def rag_config() -> dict:
    return dict(_CFG.get("rag", {}))


def embedding_name() -> str:
    return os.environ.get("RAG_EMBEDDINGS", _CFG["models"]["embeddings"])


def reranker_name() -> str:
    return os.environ.get("RAG_RERANKER", _CFG["models"]["reranker"])


@lru_cache(maxsize=4)
def _embedder(name: str):
    from fastembed import TextEmbedding
    return TextEmbedding(name, cache_dir=CACHE, threads=os.cpu_count())


@lru_cache(maxsize=4)
def _reranker(name: str):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(name, cache_dir=CACHE, threads=os.cpu_count())


def _norm(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)


def embed_documents(texts: list[str], name: str | None = None) -> np.ndarray:
    name = name or embedding_name()
    pre = PREFIXES.get(name, ("", ""))[1]
    return _norm(np.array(list(_embedder(name).embed([pre + t for t in texts], batch_size=32)), dtype=np.float32))


def embed_queries(texts: list[str], name: str | None = None) -> np.ndarray:
    name = name or embedding_name()
    pre = PREFIXES.get(name, ("", ""))[0]
    return _norm(np.array(list(_embedder(name).embed([pre + t for t in texts], batch_size=32)), dtype=np.float32))


class _ScoreCache:
    """Disk cache of cross-encoder scores keyed by (model, query, passage), so evaluation
    reruns and ablations do not recompute pairs they have already scored."""

    def __init__(self, path: Path):
        import sqlite3
        import threading
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS s (k TEXT PRIMARY KEY, v REAL)")
        self.lock = threading.Lock()

    def get_many(self, keys: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            q = f"SELECT k, v FROM s WHERE k IN ({','.join('?' * len(part))})"
            out.update(dict(self.db.execute(q, part).fetchall()))
        return out

    def put_many(self, items: dict[str, float]) -> None:
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO s VALUES (?, ?)", list(items.items()))
            self.db.commit()


_SCORES = _ScoreCache(Path(os.environ.get("RAG_SCORE_CACHE", ROOT / "data/processed/rerank_cache.sqlite3")))


def rerank_scores(query: str, passages: list[str], name: str | None = None) -> list[float]:
    """Cross-encoder relevance logits (higher = more relevant), cached on disk."""
    if not passages:
        return []
    import hashlib
    name = name or reranker_name()
    keys = [hashlib.sha1(f"{name}\x1f{query}\x1f{p}".encode()).hexdigest() for p in passages]
    have = _SCORES.get_many(keys)
    todo = [j for j, k in enumerate(keys) if k not in have]
    if todo:
        new = [float(x) for x in _reranker(name).rerank(query, [passages[j] for j in todo], batch_size=16)]
        fresh = {keys[j]: v for j, v in zip(todo, new)}
        _SCORES.put_many(fresh)
        have.update(fresh)
    return [have[k] for k in keys]


def sigmoid(x: float) -> float:
    return float(1 / (1 + np.exp(-x)))
