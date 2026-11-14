Embeddings `BAAI/bge-small-en-v1.5`, reranker `jinaai/jina-reranker-v1-turbo-en`, pool `all`, generated 2026-11-14T04:45:31Z.

| Variant | Persona | Recall@5 | MRR | nDCG@10 | ProjRecall@3 | p95 latency (s) |
|---|---|---|---|---|---|---|
| v0_naive | ai_ml | 0.062 | 0.114 | 0.071 | 0.460 | 0.26 |
| v0_naive | fullstack | 0.063 | 0.109 | 0.071 | 0.583 | 0.17 |
| v0_naive | data_analyst | 0.080 | 0.121 | 0.118 | 0.446 | 0.22 |
| v0_naive | ALL | 0.068 | 0.114 | 0.085 | 0.499 | 0.22 |
| v2_hybrid | ai_ml | 0.086 | 0.111 | 0.091 | 0.415 | 0.15 |
| v2_hybrid | fullstack | 0.065 | 0.124 | 0.081 | 0.549 | 0.16 |
| v2_hybrid | data_analyst | 0.115 | 0.149 | 0.149 | 0.443 | 0.16 |
| v2_hybrid | ALL | 0.087 | 0.127 | 0.104 | 0.470 | 0.16 |
| v4_outcome | ai_ml | 0.562 | 0.616 | 0.628 | 0.292 | 0.15 |
| v4_outcome | fullstack | 0.497 | 0.560 | 0.569 | 0.402 | 0.16 |
| v4_outcome | data_analyst | 0.459 | 0.601 | 0.555 | 0.299 | 0.16 |
| v4_outcome | ALL | 0.509 | 0.592 | 0.585 | 0.333 | 0.16 |
