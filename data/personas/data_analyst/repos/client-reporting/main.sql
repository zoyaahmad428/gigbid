-- Self-serve reporting in Looker Studio for a hospitality agency

-- incremental load
MERGE INTO mart.orders t
USING staging.orders s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status
WHEN NOT MATCHED THEN INSERT ROW;


-- feeds the dashboard's trend chart
SELECT DATE_TRUNC('week', ts) AS wk, AVG(delay_min) FROM ops.shipments GROUP BY 1;
