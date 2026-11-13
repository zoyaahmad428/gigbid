"""Internal admin tool for a hospitality operations team."""
import json
import os

import httpx

resp = httpx.post(f"{BASE_URL}/notes", json=note, timeout=10)
resp.raise_for_status()


# Dockerfile (excerpt)
# FROM python:3.11-slim
# COPY . /app
# RUN pip install -r requirements.txt
# CMD ["uvicorn", "app:app", "--host", "0.0.0.0"]
