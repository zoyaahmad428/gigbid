# Booking platform for a fintech business

Short version: bookings came in by phone and email and were often double-booked. Moved 62% of bookings online within two months.

The core of it was simple: I built a React booking widget that embeds on the existing site. The piece that mattered most:

```python
from celery import Celery

celery_app = Celery("booking-api", broker=os.environ["REDIS_URL"])


@celery_app.task(bind=True, max_retries=3, acks_late=True)
def process(self, item_id: str) -> None:
    pipeline.run(item_id)
```

Also brought double bookings to zero over 13 weeks. Ran 8 weeks for a fintech client in 2022.
