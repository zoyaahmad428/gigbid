# A/B test design and analysis for a non-profit product team

Short version: tests were stopped early and results were read from dashboards. Reviewed 12 past tests and found half were underpowered.

The core of it was simple: I analysed results with confidence intervals and guardrail metrics. The piece that mattered most:

```python
import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()
```

Also the shipped variant raised conversion by 3%. Ran 16 weeks for a non-profit client in 2023.
