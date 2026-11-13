# Model deployment and monitoring setup for a hospitality team

Short version: models were retrained by hand on a laptop and nobody noticed drift. Cut release time for a new model from 4 days to 2.

The core of it was simple: I served models behind an API with versioned releases. The piece that mattered most:

```python
from fastapi import FastAPI

app = FastAPI(title="ml-platform-utils")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)
```

Also caught 4 data-quality incidents before they reached users. Ran 5 weeks for a hospitality client in 2025.
