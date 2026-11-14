# RAG variants

Each file defines one `Config` of the shared pipeline in `rag/pipeline.py`. `rag/retriever.py`
registers them by name. `make rag-suite` reruns the whole comparison (`evals/runners/run_rag_suite.sh`),
and `--ablation` removes one component at a time from a combination.

| ID | File | Component | Group |
|---|---|---|---|
| V0 | `v0_naive.py` | Fixed chunks, dense, whole post as the query | Baseline |
| V1 | `v1_decompose.py` | One query per requirement, fused with RRF | Query (required) |
| V2 | `v2_hybrid.py` | BM25 + dense with RRF | Retrieval (required) |
| V3 | `v3_rerank.py` | Cross-encoder reranking | Retrieval (required) |
| V4 | `v4_outcome.py` | Past proposals pass their relevance, weighted by outcome, to the projects they cited | Ranking |
| V5 | `v5_multivector.py` | Project-level grouping of summary, README and code | Indexing |
| V8 | `v8_corrective.py` | Grade, re-query, refuse when nothing supports a claim | Agentic (required: V8 or V9) |
| — | `combined.py` | V1 + V2 + V4 + V5 (**deployed**, `rag.variant` in `agent/config.yaml`) | |

V6 (contextual retrieval), V7 (HyDE) and V9 (skill graph) are not implemented.

## Results

Setup:
- **Data.** The dev sets in `evals/datasets/dev_{persona}.jsonl`, scored on the jobs whose gold action is "bid" (100 / 100 / 85).
- **Gold labels.** The documents of the projects that best match each job's skills (IDF-weighted overlap), from `data/pipeline/synth/gigsim.py`.
- **Metrics.** Recall@5 is measured against min(5, number of gold documents).
- **Models.** Embeddings are `BAAI/bge-small-en-v1.5` and the cross-encoder is `jinaai/jina-reranker-v1-turbo-en`, both on CPU through fastembed.
- **Latency.** p95 is the worst persona's value, per job, on an 8-core laptop.

Raw results are in `evals/results/rag_retrieval_*.{md,json}` with per-job rows in `*_rows.csv`.

### Single components

| Variant | R@5 AI/ML | R@5 full-stack | R@5 data | nDCG@10 (mean) | MRR (mean) | p95 latency |
|---|---|---|---|---|---|---|
| v0_naive | 0.324 | 0.368 | 0.412 | 0.465 | 0.468 | 0.19 s |
| v1_decompose | 0.304 | 0.395 | 0.419 | 0.465 | 0.488 | 0.15 s |
| v2_hybrid | 0.400 | 0.383 | 0.438 | 0.508 | 0.513 | 0.42 s |
| v3_rerank | 0.326 | 0.378 | 0.461 | 0.484 | 0.508 | 3.13 s |
| v4_outcome | 0.562 | 0.497 | 0.459 | 0.584 | 0.592 | 0.20 s |
| v5_multivector | 0.330 | 0.433 | 0.447 | 0.502 | 0.413 | 0.20 s |
| v8_corrective | 0.288 | 0.321 | 0.259 | 0.351 | 0.370 | 2.97 s |

### Combinations and ablation

| Variant | R@5 AI/ML | R@5 full-stack | R@5 data | nDCG@10 (mean) | MRR (mean) | p95 latency |
|---|---|---|---|---|---|---|
| combined + V3 rerank | 0.606 | 0.575 | 0.605 | 0.678 | 0.651 | 6.53 s |
| **combined** (V1+V2+V4+V5) | 0.604 | 0.577 | 0.584 | 0.671 | 0.641 | 0.21 s |
| combined − decompose | 0.566 | 0.522 | 0.475 | 0.621 | 0.568 | 0.17 s |
| combined − bm25 | 0.550 | 0.459 | 0.588 | 0.626 | 0.587 | 0.16 s |
| combined − dense | 0.678 | 0.511 | 0.579 | 0.682 | 0.646 | 0.01 s |
| combined − outcome | 0.428 | 0.480 | 0.468 | 0.563 | 0.451 | 0.17 s |
| combined − multivector | 0.586 | 0.533 | 0.572 | 0.648 | 0.677 | 0.15 s |

### Embedding model (V0, V2)

| Variant | R@5 AI/ML | R@5 full-stack | R@5 data | nDCG@10 (mean) | MRR (mean) | p95 latency |
|---|---|---|---|---|---|---|
| v0_naive | 0.324 | 0.368 | 0.412 | 0.465 | 0.468 | 0.19 s |
| v0_naive (nomic) | 0.248 | 0.287 | 0.353 | 0.416 | 0.412 | 0.33 s |
| v2_hybrid | 0.400 | 0.383 | 0.438 | 0.508 | 0.513 | 0.42 s |
| v2_hybrid (nomic) | 0.340 | 0.299 | 0.402 | 0.468 | 0.443 | 0.32 s |

### Pool: proposals allowed in the results (pool=all)

| Variant | R@5 AI/ML | R@5 full-stack | R@5 data | nDCG@10 (mean) | MRR (mean) | p95 latency |
|---|---|---|---|---|---|---|
| v0_naive | 0.324 | 0.368 | 0.412 | 0.465 | 0.468 | 0.19 s |
| v0_naive (pool=all) | 0.062 | 0.063 | 0.080 | 0.087 | 0.115 | 0.26 s |
| v2_hybrid | 0.400 | 0.383 | 0.438 | 0.508 | 0.513 | 0.42 s |
| v2_hybrid (pool=all) | 0.086 | 0.065 | 0.115 | 0.107 | 0.128 | 0.16 s |


V8 refusals (it refuses when the best graded evidence is below `rag.corrective`):

| Persona | Refuses no-fit jobs | Wrongly refuses bid jobs |
|---|---|---|
| AI/ML | 0.90 | 0.31 |
| Full-stack | 0.74 | 0.20 |
| Data analyst | 0.81 | 0.51 |

## What we conclude

1. **Outcome signals (V4) are the strongest single component.** V4 adds +0.12 to +0.24 Recall@5 over V0, and removing it from the combination costs the most (−0.18 AI/ML).
   Past proposals act as a bridge: a job resembles earlier jobs, and the proposals for them cite the right project.
   A bug hid this at first. Proposals were filtered out of the *candidates* as well as the results, so the transfer never ran. The fix is in `rag/pipeline.py` (`cand_props`).
2. **Decomposition (V1) only helps in combination.** On its own it is roughly neutral (−0.02 to +0.03).
   Inside the combination, removing it costs −0.04 to −0.11. Each requirement finds its own project, and V4 and V5 then lift the right project.
   It was also hurt by a bug: requirements under 25 characters, such as "It must cite sources.", were dropped. The minimum is now 12.
3. **Reranking (V3) is not worth it here.** It adds at most +0.02 Recall@5 on top of the combination, and p95 latency goes from 0.2 s to 6.5 s on CPU.
   The Fit Matcher still uses the cross-encoder, but only to grade the few passages per requirement it needs for refusing (V8), not to reorder 30 candidates per query.
4. **BM25 carries most of the hybrid.** Dropping dense retrieval from the combination ties it on Recall@5 (0.589 vs 0.588 mean) at 0.01 s.
   We still deploy the hybrid. The persona documents are generated from the same skill vocabulary as the job posts, so this data favours exact word matching more than real portfolios would.
   Dense retrieval is the safety net for paraphrase ("cut handling time" vs "reduced support load"), and the combination is the least uneven across personas.
5. **The larger embedding model did worse.** `nomic-embed-text-v1.5` (quantised) is worse than bge-small on every persona, by 0.04 to 0.08 Recall@5, and slower.
6. **Proposals must not be returned as evidence.** With `pool=all`, proposals take most of the top-5 slots and recall falls to about 0.07.
   A past proposal is a claim the user made before, not proof, so the deployed setting returns portfolio documents only.
7. **V8 trades recall for refusals.** It refuses 74–90% of jobs the persona can't do, but also refuses 20–51% of jobs it should bid on.
   So the bid graph uses a stricter rule. The Fit Matcher grades each requirement separately, and refuses only when *every* requirement is "missing" *and* a retry with the job's skills finds nothing either.
   Weaker cases lower the fit score and are left to the Decision node's thresholds.

**Caveat.** The variant was chosen on the same dev set it is reported on. The final comparison in the report re-runs `combined` and V0 on a held-out split (step 7).
