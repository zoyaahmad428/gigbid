# Marketing channel attribution analysis for a B2B SaaS brand

Short version: the team could not say which channels drove sales. Found that 34% of spend went to channels with no measurable return.

The core of it was simple: I compared last-click with a position-based model. The piece that mattered most:

```python
import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()
```

Also the reallocated budget lowered cost per order by 18%. Ran 9 weeks for a B2B SaaS client in 2024.
