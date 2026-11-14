"""Combined (deployed): V1 decompose + V2 hybrid + V4 outcome + V5 multi-vector, no cross-encoder.

Chosen from the ablation in evals/results/rag_retrieval_fixed_ablation.md: adding the V3
reranker on top (best_full) changes Recall@5 by at most +0.02 while p95 latency goes from
~0.2 s to 3.6-6.5 s per job on CPU. The Fit Matcher still uses the cross-encoder, but only to
grade the evidence of each requirement (V8 logic), not to reorder the whole candidate list.
"""
from rag.pipeline import Config

CONFIG = Config(name="combined", query="decompose", retrieval="hybrid", outcome=True, multivector=True)
