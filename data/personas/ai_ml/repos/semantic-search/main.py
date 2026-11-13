"""Semantic product search for a manufacturing catalogue."""
import json
import os

from scipy import stats

t, p = stats.ttest_ind(control, variant, equal_var=False)
lift = variant.mean() / control.mean() - 1


# Dockerfile (excerpt)
# FROM python:3.11-slim
# COPY . /app
# RUN pip install -r requirements.txt
# CMD ["uvicorn", "app:app", "--host", "0.0.0.0"]
