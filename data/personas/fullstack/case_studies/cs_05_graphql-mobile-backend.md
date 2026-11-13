# GraphQL backend for a insurance mobile app

**Client:** insurance company  
**When:** 2021-07 to 2021-07  
**Engagement:** fixed-price

## Situation
The app made many slow REST calls per screen.

## What I did
- Designed a GraphQL schema around the app's screens.
- Added batching and caching to remove repeated queries.
- Set up auth with refresh tokens.

## Outcome
- Cut API calls per screen from 11 to 2.
- Brought p95 API latency under 202 ms.

## Stack
GraphQL · Node.js · TypeScript
