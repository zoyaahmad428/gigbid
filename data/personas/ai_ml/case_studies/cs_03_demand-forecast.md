# Weekly demand forecasting for a real estate business

Short version: stock planning relied on last year's numbers and missed seasonal swings. Lowered forecast error (MAPE) from 17% to 11%.

The core of it was simple: I compared gradient-boosted trees with a seasonal naive baseline. The piece that mattered most:

```python
import pandas as pd

df = pd.read_parquet(path)
df = df.dropna(subset=["id"]).drop_duplicates("id")
weekly = df.groupby(pd.Grouper(key="date", freq="W"))["amount"].sum()
```

Also reduced overstock by about 11%. Ran 16 weeks for a real estate client in 2026.
