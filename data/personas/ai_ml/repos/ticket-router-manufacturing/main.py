"""Ticket triage classifier for a manufacturing help desk."""
import json
import os

from peft import LoraConfig, get_peft_model

lora = LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj", "v_proj"])
model = get_peft_model(base_model, lora)


import mlflow

with mlflow.start_run(run_name="train"):
    mlflow.log_params(params)
    model = train(params)
    mlflow.log_metric("val_f1", evaluate(model))
    mlflow.sklearn.log_model(model, "model")
