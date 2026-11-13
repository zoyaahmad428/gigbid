# app-graphql

GraphQL backend for a insurance mobile app.

## What it does
The app made many slow REST calls per screen. This is the code from when I added batching and caching to remove repeated queries.

## Stack
Apollo server, schema and resolvers; GraphQL, Node.js, TypeScript.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: cut API calls per screen from 11 to 2.
