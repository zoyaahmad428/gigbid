"""V4 outcome-aware ranking: past proposals boost the projects they cited, weighted by won/replied/ignored."""
from rag.pipeline import Config

CONFIG = Config(name="v4_outcome", query="post", retrieval="dense", outcome=True)
