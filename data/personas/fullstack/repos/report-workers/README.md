# report-workers

Background job system for a B2B SaaS reporting product.

## What it does
Large reports timed out inside web requests. This is the code from when I added progress updates to the UI.

## Stack
task definitions and progress API; Django, REST API, React.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: removed all report timeouts (49 per week before).
