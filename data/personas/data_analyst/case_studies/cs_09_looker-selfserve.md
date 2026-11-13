# Self-serve reporting in Looker Studio for a hospitality agency

Short version: account managers asked the analyst for the same reports every week. Cut analyst report requests by 74%.

The core of it was simple: I designed Looker Studio templates per client type. The piece that mattered most:

```sql
-- incremental load
MERGE INTO mart.orders t
USING staging.orders s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status
WHEN NOT MATCHED THEN INSERT ROW;
```

Also served 38 client accounts from one template. Ran 11 weeks for a hospitality client in 2022.
