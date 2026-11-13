"""Customer cohort and retention analysis for a insurance app."""
import json
import os

import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()


import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot(cohorts.index, cohorts["retention_30d"])
fig.savefig("retention.png")
