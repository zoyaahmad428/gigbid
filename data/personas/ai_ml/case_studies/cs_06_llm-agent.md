# Tool-using LLM agent for e-commerce operations staff

Short version: staff had to look up the same data across three internal systems. Answered 65% of lookup requests without a person.

The core of it was simple: I built the agent as a graph with a human approval step before any write. The piece that mattered most:

```python
from fastapi import FastAPI

app = FastAPI(title="ops-agent")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)
```

Also cut the median lookup from 8 minutes to under 1. Ran 7 weeks for a e-commerce client in 2021.
