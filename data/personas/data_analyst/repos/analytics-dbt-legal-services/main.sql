-- Analytics warehouse with dbt for a legal services startup

SELECT region, SUM(revenue) AS revenue, COUNT(DISTINCT customer_id) AS customers
FROM mart.sales GROUP BY region;
