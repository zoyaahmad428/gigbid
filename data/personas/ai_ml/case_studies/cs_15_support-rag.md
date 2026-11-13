# RAG assistant over manufacturing support documentation

Short version: support agents spent too long searching the help-centre articles for answers to repeat questions. Cut average handling time by 29%.

The core of it was simple: I combined BM25 and dense retrieval and reranked the top results. The piece that mattered most:

```python
from fastapi import FastAPI

app = FastAPI(title="docs-qa-service-manufacturing")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)
```

Also reached 84% answer accuracy on a 200-question test set. Ran 15 weeks for a manufacturing client in 2024.
