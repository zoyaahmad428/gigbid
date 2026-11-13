"""Tool-using LLM agent for e-commerce operations staff."""
import json
import os

from fastapi import FastAPI

app = FastAPI(title="ops-agent")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)


import psycopg

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("INSERT INTO audit_log (tool, args) VALUES (%s, %s)", (tool, json.dumps(args)))
