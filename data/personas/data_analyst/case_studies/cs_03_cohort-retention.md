# Customer cohort and retention analysis for a media app

Short version: growth looked healthy but revenue was flat. Found a 50% drop-off at one onboarding step.

The core of it was simple: I measured retention and revenue per cohort. The piece that mattered most:

```python
import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()
```

Also the fix raised 30-day retention by 3 points. Ran 16 weeks for a media client in 2021.
