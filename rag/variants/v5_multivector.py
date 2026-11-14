"""V5 multi-vector items: summary, README and code indexed separately, returned together as one project."""
from rag.pipeline import Config

CONFIG = Config(name="v5_multivector", query="post", retrieval="dense", multivector=True)
