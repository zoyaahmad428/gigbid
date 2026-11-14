Embeddings `BAAI/bge-small-en-v1.5`, reranker `jinaai/jina-reranker-v1-turbo-en`, pool `portfolio`, generated 2026-11-14T05:07:20Z.

| Variant | Persona | Recall@5 | MRR | nDCG@10 | ProjRecall@3 | p95 latency (s) |
|---|---|---|---|---|---|---|
| dhom | ai_ml | 0.604 | 0.676 | 0.682 | 0.292 | 0.21 |
| dhom | fullstack | 0.577 | 0.622 | 0.651 | 0.380 | 0.15 |
| dhom | data_analyst | 0.584 | 0.626 | 0.680 | 0.271 | 0.12 |
| dhom | ALL | 0.588 | 0.642 | 0.670 | 0.317 | 0.16 |
| dhom-decompose | ai_ml | 0.566 | 0.629 | 0.662 | 0.278 | 0.16 |
| dhom-decompose | fullstack | 0.522 | 0.554 | 0.608 | 0.332 | 0.17 |
| dhom-decompose | data_analyst | 0.475 | 0.522 | 0.593 | 0.221 | 0.17 |
| dhom-decompose | ALL | 0.523 | 0.571 | 0.623 | 0.280 | 0.16 |
| dhom-bm25 | ai_ml | 0.550 | 0.620 | 0.640 | 0.268 | 0.15 |
| dhom-bm25 | fullstack | 0.459 | 0.490 | 0.578 | 0.330 | 0.14 |
| dhom-bm25 | data_analyst | 0.588 | 0.651 | 0.661 | 0.267 | 0.16 |
| dhom-bm25 | ALL | 0.529 | 0.584 | 0.625 | 0.289 | 0.15 |
| dhom-dense | ai_ml | 0.678 | 0.753 | 0.752 | 0.337 | 0.01 |
| dhom-dense | fullstack | 0.511 | 0.548 | 0.626 | 0.316 | 0.00 |
| dhom-dense | data_analyst | 0.579 | 0.637 | 0.669 | 0.279 | 0.00 |
| dhom-dense | ALL | 0.590 | 0.647 | 0.683 | 0.313 | 0.00 |
| dhom-outcome | ai_ml | 0.428 | 0.416 | 0.560 | 0.213 | 0.17 |
| dhom-outcome | fullstack | 0.480 | 0.479 | 0.545 | 0.315 | 0.16 |
| dhom-outcome | data_analyst | 0.468 | 0.457 | 0.585 | 0.216 | 0.11 |
| dhom-outcome | ALL | 0.458 | 0.450 | 0.562 | 0.250 | 0.14 |
| dhom-multivector | ai_ml | 0.586 | 0.698 | 0.661 | 0.374 | 0.14 |
| dhom-multivector | fullstack | 0.533 | 0.663 | 0.627 | 0.443 | 0.15 |
| dhom-multivector | data_analyst | 0.572 | 0.669 | 0.657 | 0.378 | 0.13 |
| dhom-multivector | ALL | 0.563 | 0.677 | 0.648 | 0.399 | 0.14 |
