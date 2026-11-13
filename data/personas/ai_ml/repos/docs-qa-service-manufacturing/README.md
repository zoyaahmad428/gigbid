# docs-qa-service-manufacturing

RAG assistant over manufacturing support documentation.

## What it does
Support agents spent too long searching the help-centre articles for answers to repeat questions. This is the code from when I combined BM25 and dense retrieval and reranked the top results.

## Stack
FastAPI service, hybrid retrieval, Docker image; Python, RAG, LLM, LangChain, Vector Database.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: cut average handling time by 29%.
