"""V0 baseline: fixed word chunks, dense top-k cosine on the whole job post."""
from rag.pipeline import Config

CONFIG = Config(name="v0_naive", query="post", retrieval="dense")
