# Customer survey analysis for a non-profit organisation

Short version: thousands of survey responses sat unread every quarter. Analysed 6190 responses.

The core of it was simple: I coded open-text answers into themes. The piece that mattered most:

```python
import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()
```

Also identified 3 drivers that explained most dissatisfaction. Ran 3 weeks for a non-profit client in 2022.
