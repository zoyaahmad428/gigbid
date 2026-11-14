"""Chunked dense index over one persona's documents, cached on disk.

Chunks are fixed word windows (V0's "fixed chunks"). A document's score for a
query is its best chunk's cosine similarity. Embeddings are cached in
data/processed/index/ keyed by model and a hash of the chunk texts, so
re-running an evaluation does not re-embed unchanged documents.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from rag.ingest.corpus import Doc
from rag.models import ROOT, embed_documents, embedding_name

INDEX_DIR = ROOT / "data/processed/index"


def chunk_words(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [" ".join(words)]
    step = max(1, size - overlap)
    return [" ".join(words[i:i + size]) for i in range(0, max(1, len(words) - overlap), step)]


class DenseIndex:
    def __init__(self, docs: list[Doc], *, size: int = 120, overlap: int = 30, model: str | None = None,
                 prefix_title: bool = True, contexts: list[str] | None = None):
        self.model = model or embedding_name()
        self.chunks: list[str] = []
        self.owner: list[int] = []
        for i, d in enumerate(docs):
            head = f"{d.title}\n" if prefix_title else ""
            ctx = f"{contexts[i]}\n" if contexts else ""
            for c in chunk_words(d.text, size, overlap):
                self.chunks.append(ctx + head + c)
                self.owner.append(i)
        self.owner_arr = np.array(self.owner)
        self.n_docs = len(docs)
        key = hashlib.sha256(("\x1f".join(self.chunks) + self.model).encode()).hexdigest()[:20]
        path = INDEX_DIR / f"{self.model.replace('/', '__')}_{key}.npy"
        if path.exists():
            self.emb = np.load(path)
        else:
            self.emb = embed_documents(self.chunks, self.model)
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, self.emb)

    def doc_scores(self, qvec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Best-chunk score per document and the index of that chunk (-1 if none)."""
        sims = self.emb @ qvec
        order = np.lexsort((-sims, self.owner_arr))        # by document, best chunk first
        docs_sorted = self.owner_arr[order]
        _, first = np.unique(docs_sorted, return_index=True)
        best = np.full(self.n_docs, -1.0, dtype=np.float32)
        best_chunk = np.full(self.n_docs, -1, dtype=np.int64)
        winners = order[first]
        best[self.owner_arr[winners]] = sims[winners]
        best_chunk[self.owner_arr[winners]] = winners
        return best, best_chunk
