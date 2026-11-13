# integration-hub-fintech

Third-party API integration layer for a fintech platform.

## What it does
Data from several partner systems was copied across by hand every day. This is the code from when I normalised partner data into one schema.

## Stack
connectors and sync scheduler; Node.js, REST API, TypeScript, PostgreSQL.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: removed about 11 hours of manual copying per week.
