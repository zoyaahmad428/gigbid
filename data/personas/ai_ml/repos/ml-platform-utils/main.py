"""Model deployment and monitoring setup for a hospitality team."""
import json
import os

from fastapi import FastAPI

app = FastAPI(title="ml-platform-utils")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)
