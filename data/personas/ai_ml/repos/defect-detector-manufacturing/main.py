"""Visual defect detection on a manufacturing production line."""
import json
import os

# Dockerfile (excerpt)
# FROM python:3.11-slim
# COPY . /app
# RUN pip install -r requirements.txt
# CMD ["uvicorn", "app:app", "--host", "0.0.0.0"]


import mlflow

with mlflow.start_run(run_name="train"):
    mlflow.log_params(params)
    model = train(params)
    mlflow.log_metric("val_f1", evaluate(model))
    mlflow.sklearn.log_model(model, "model")
