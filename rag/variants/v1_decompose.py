"""V1 requirement decomposition: one dense retrieval per extracted requirement, fused with RRF."""
from rag.pipeline import Config

CONFIG = Config(name="v1_decompose", query="decompose", retrieval="dense")
