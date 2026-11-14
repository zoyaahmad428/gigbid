#!/usr/bin/env bash
# Full RAG comparison: every variant, two combined pipelines with ablations,
# the second embedding model, and the "all documents" pool. Writes evals/results/rag_retrieval_*.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-.venv/bin/python}
$PY -m evals.runners.rag_eval --tag variants
$PY -m evals.runners.rag_eval --variants --combo best_fast=hybrid,outcome,multivector \
    --combo best_full=decompose,hybrid,rerank,outcome,multivector --ablation --tag ablation
$PY -m evals.runners.rag_eval --variants v0_naive v2_hybrid --embeddings nomic-ai/nomic-embed-text-v1.5-Q --tag nomic
$PY -m evals.runners.rag_eval --variants v0_naive v2_hybrid v4_outcome --pool all --tag pool_all
# claim verification runs after retrieval, never in parallel (each process loads the models; 5 GB RAM)
$PY -m evals.runners.verify_eval
