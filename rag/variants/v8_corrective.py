"""V8 corrective RAG: grade the evidence, re-query on the job's skills when weak, refuse to claim when none.

The loop runs inside the pipeline here so it can be evaluated on its own; the
Fit Matcher node in the LangGraph bid graph uses the same grade/re-query/refuse
steps as explicit graph nodes.
"""
from rag.pipeline import Config

CONFIG = Config(name="v8_corrective", query="post", retrieval="dense", corrective=True)
