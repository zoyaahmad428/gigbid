"""Meeting and call summarisation for a B2B SaaS sales team."""
import json
import os

from celery import Celery

celery_app = Celery("call-notes", broker=os.environ["REDIS_URL"])


@celery_app.task(bind=True, max_retries=3, acks_late=True)
def process(self, item_id: str) -> None:
    pipeline.run(item_id)


import redis

cache = redis.Redis.from_url(os.environ["REDIS_URL"])


def cached(key: str, ttl: int = 3600):
    hit = cache.get(key)
    return json.loads(hit) if hit else None


import httpx

resp = httpx.post(f"{BASE_URL}/notes", json=note, timeout=10)
resp.raise_for_status()
