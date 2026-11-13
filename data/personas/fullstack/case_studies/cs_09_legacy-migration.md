# Legacy PHP to modern stack migration for a B2B SaaS client

Short version: a ten-year-old php app was hard to change and had no tests. Migrated 6 modules with no downtime.

The core of it was simple: I rewrote the moved modules as a Django API with a React frontend. The piece that mattered most:

```python
# Dockerfile (excerpt)
# FROM python:3.11-slim
# COPY . /app
# RUN pip install -r requirements.txt
# CMD ["uvicorn", "app:app", "--host", "0.0.0.0"]
```

Also raised test coverage from 0 to 75%. Ran 15 weeks for a B2B SaaS client in 2026.
