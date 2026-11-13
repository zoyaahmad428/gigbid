-- Operations KPI dashboard in Tableau for a hospitality company

-- incremental load
MERGE INTO mart.orders t
USING staging.orders s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status
WHEN NOT MATCHED THEN INSERT ROW;
