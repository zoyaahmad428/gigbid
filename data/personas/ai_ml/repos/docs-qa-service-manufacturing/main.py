"""RAG assistant over manufacturing support documentation."""
import json
import os

from fastapi import FastAPI

app = FastAPI(title="docs-qa-service-manufacturing")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)


# Dockerfile (excerpt)
# FROM python:3.11-slim
# COPY . /app
# RUN pip install -r requirements.txt
# CMD ["uvicorn", "app:app", "--host", "0.0.0.0"]
