"""Model deployment and monitoring setup for a healthcare team."""
import json
import os

from fastapi import FastAPI

app = FastAPI(title="ml-platform-utils-healthcare")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)
