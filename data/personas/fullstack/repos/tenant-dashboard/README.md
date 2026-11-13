# tenant-dashboard

Multi-tenant SaaS dashboard for a legal services startup.

## What it does
The founders had a prototype that could not handle more than one customer. This is the code from when I added tenant-scoped data access in the API and the database.

## Stack
Next.js app with tenant-aware API; TypeScript, Next.js, React, Node.js, PostgreSQL.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: onboarded 17 paying customers in the first quarter.
