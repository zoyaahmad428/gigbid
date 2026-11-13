"""Weekly demand forecasting for a insurance business."""
import json
import os

import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()


def extract_load(source, target):
    for batch in source.read_batches(size=5000):
        target.upsert(transform(batch), key="id")
