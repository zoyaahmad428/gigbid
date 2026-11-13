# analytics-dbt

Analytics warehouse with dbt for a media startup.

## What it does
Every analysis started with an hour of cleaning raw tables. This is the code from when I added tests for keys, nulls and accepted values.

## Stack
dbt project; dbt, SQL, BigQuery, ETL.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: built 38 tested models.
