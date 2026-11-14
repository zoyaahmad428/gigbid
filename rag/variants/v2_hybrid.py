"""V2 hybrid: BM25 plus dense, reciprocal rank fusion. Targets exact tool names embeddings blur."""
from rag.pipeline import Config

CONFIG = Config(name="v2_hybrid", query="post", retrieval="hybrid")
