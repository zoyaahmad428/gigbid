"""Churn prediction model for a edtech subscription product."""
import json
import os

QUERY = """
SELECT account_id, COUNT(*) AS events_30d
FROM events
WHERE ts >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY account_id
"""


import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()
