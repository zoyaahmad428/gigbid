"""Data cleaning and deduplication for a insurance CRM."""
import json
import os

QUERY = """
SELECT account_id, COUNT(*) AS events_30d
FROM events
WHERE ts >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY account_id
"""


df.to_excel("report.xlsx", sheet_name="summary", index=False)
