"""V3 cross-encoder reranking of the dense candidates (precision at small k)."""
from rag.pipeline import Config

CONFIG = Config(name="v3_rerank", query="post", retrieval="dense", rerank=True)
