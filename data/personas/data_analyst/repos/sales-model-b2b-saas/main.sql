-- Sales performance dashboard for a B2B SaaS company

-- incremental load
MERGE INTO mart.orders t
USING staging.orders s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status
WHEN NOT MATCHED THEN INSERT ROW;


-- exported to the finance workbook through Power Query
SELECT * FROM reporting.weekly_summary;
