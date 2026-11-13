"""Invoice and form data extraction for a recruiting back office."""
import json
import os

from fastapi import FastAPI

app = FastAPI(title="doc-extractor")


@app.post("/v1/run")
async def run(payload: dict) -> dict:
    return service.handle(payload)


from celery import Celery

celery_app = Celery("doc-extractor", broker=os.environ["REDIS_URL"])


@celery_app.task(bind=True, max_retries=3, acks_late=True)
def process(self, item_id: str) -> None:
    pipeline.run(item_id)


import redis

cache = redis.Redis.from_url(os.environ["REDIS_URL"])


def cached(key: str, ttl: int = 3600):
    hit = cache.get(key)
    return json.loads(hit) if hit else None
