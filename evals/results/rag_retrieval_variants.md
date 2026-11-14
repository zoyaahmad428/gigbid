Embeddings `BAAI/bge-small-en-v1.5`, reranker `jinaai/jina-reranker-v1-turbo-en`, pool `portfolio`, generated 2026-11-14T03:57:58Z.

| Variant | Persona | Recall@5 | MRR | nDCG@10 | ProjRecall@3 | p95 latency (s) |
|---|---|---|---|---|---|---|
| v0_naive | ai_ml | 0.324 | 0.391 | 0.429 | 0.353 | 0.13 |
| v0_naive | fullstack | 0.368 | 0.512 | 0.451 | 0.436 | 0.15 |
| v0_naive | data_analyst | 0.412 | 0.502 | 0.516 | 0.384 | 0.19 |
| v0_naive | ALL | 0.366 | 0.467 | 0.463 | 0.391 | 0.16 |
| v1_decompose | ai_ml | 0.310 | 0.435 | 0.424 | 0.340 | 0.12 |
| v1_decompose | fullstack | 0.382 | 0.502 | 0.461 | 0.462 | 0.15 |
| v1_decompose | data_analyst | 0.433 | 0.523 | 0.517 | 0.427 | 0.10 |
| v1_decompose | ALL | 0.372 | 0.485 | 0.465 | 0.409 | 0.12 |
| v2_hybrid | ai_ml | 0.400 | 0.486 | 0.501 | 0.436 | 0.17 |
| v2_hybrid | fullstack | 0.383 | 0.540 | 0.471 | 0.456 | 0.28 |
| v2_hybrid | data_analyst | 0.438 | 0.513 | 0.551 | 0.405 | 0.42 |
| v2_hybrid | ALL | 0.405 | 0.513 | 0.505 | 0.434 | 0.33 |
| v3_rerank | ai_ml | 0.326 | 0.371 | 0.428 | 0.365 | 1.71 |
| v3_rerank | fullstack | 0.378 | 0.545 | 0.463 | 0.508 | 2.59 |
| v3_rerank | data_analyst | 0.461 | 0.609 | 0.561 | 0.450 | 3.13 |
| v3_rerank | ALL | 0.384 | 0.503 | 0.480 | 0.441 | 2.54 |
| v4_outcome | ai_ml | 0.376 | 0.509 | 0.461 | 0.378 | 0.13 |
| v4_outcome | fullstack | 0.374 | 0.508 | 0.442 | 0.406 | 0.14 |
| v4_outcome | data_analyst | 0.395 | 0.504 | 0.484 | 0.319 | 0.15 |
| v4_outcome | ALL | 0.381 | 0.507 | 0.461 | 0.370 | 0.14 |
| v5_multivector | ai_ml | 0.330 | 0.340 | 0.444 | 0.172 | 0.14 |
| v5_multivector | fullstack | 0.433 | 0.451 | 0.512 | 0.271 | 0.18 |
| v5_multivector | data_analyst | 0.447 | 0.447 | 0.549 | 0.206 | 0.20 |
| v5_multivector | ALL | 0.401 | 0.411 | 0.499 | 0.217 | 0.17 |
| v8_corrective | ai_ml | 0.288 | 0.346 | 0.362 | 0.302 | 2.80 |
| v8_corrective | fullstack | 0.321 | 0.445 | 0.396 | 0.375 | 2.77 |
| v8_corrective | data_analyst | 0.259 | 0.318 | 0.295 | 0.273 | 2.97 |
| v8_corrective | ALL | 0.291 | 0.372 | 0.354 | 0.319 | 2.77 |
