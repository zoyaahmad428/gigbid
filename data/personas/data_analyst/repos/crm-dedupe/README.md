# crm-dedupe

Data cleaning and deduplication for a insurance CRM.

## What it does
The CRM had duplicate and inconsistent records that broke reporting. This is the code from when I matched duplicates with fuzzy matching and manual review of edge cases.

## Stack
dedupe pipeline; Python, pandas, Data Analysis.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: merged about 10% duplicate records.
