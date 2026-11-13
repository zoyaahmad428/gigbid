# Analytics warehouse with dbt for a legal services startup

Short version: every analysis started with an hour of cleaning raw tables. Built 41 tested models.

The core of it was simple: I added tests for keys, nulls and accepted values. The piece that mattered most:

```sql
SELECT region, SUM(revenue) AS revenue, COUNT(DISTINCT customer_id) AS customers
FROM mart.sales GROUP BY region;
```

Also cut time to answer ad-hoc questions by about 54%. Ran 11 weeks for a legal services client in 2023.
