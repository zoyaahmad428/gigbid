Embeddings `BAAI/bge-small-en-v1.5`, reranker `jinaai/jina-reranker-v1-turbo-en`, pool `portfolio`, generated 2026-11-14T05:05:40Z.

| Variant | Persona | Recall@5 | MRR | nDCG@10 | ProjRecall@3 | p95 latency (s) |
|---|---|---|---|---|---|---|
| v1_decompose | ai_ml | 0.304 | 0.436 | 0.421 | 0.339 | 0.11 |
| v1_decompose | fullstack | 0.395 | 0.509 | 0.467 | 0.473 | 0.15 |
| v1_decompose | data_analyst | 0.419 | 0.519 | 0.507 | 0.419 | 0.10 |
| v1_decompose | ALL | 0.370 | 0.487 | 0.463 | 0.410 | 0.11 |
| v4_outcome | ai_ml | 0.562 | 0.616 | 0.628 | 0.292 | 0.17 |
| v4_outcome | fullstack | 0.497 | 0.560 | 0.569 | 0.402 | 0.20 |
| v4_outcome | data_analyst | 0.459 | 0.601 | 0.555 | 0.299 | 0.20 |
| v4_outcome | ALL | 0.509 | 0.592 | 0.585 | 0.333 | 0.19 |
| best_fast | ai_ml | 0.566 | 0.629 | 0.662 | 0.278 | 0.18 |
| best_fast | fullstack | 0.522 | 0.554 | 0.608 | 0.332 | 0.22 |
| best_fast | data_analyst | 0.475 | 0.522 | 0.593 | 0.221 | 0.27 |
| best_fast | ALL | 0.523 | 0.571 | 0.623 | 0.280 | 0.24 |
| best_full | ai_ml | 0.606 | 0.684 | 0.692 | 0.299 | 6.53 |
| best_full | fullstack | 0.575 | 0.621 | 0.653 | 0.380 | 5.30 |
| best_full | data_analyst | 0.605 | 0.648 | 0.689 | 0.271 | 3.60 |
| best_full | ALL | 0.595 | 0.651 | 0.677 | 0.319 | 5.85 |
