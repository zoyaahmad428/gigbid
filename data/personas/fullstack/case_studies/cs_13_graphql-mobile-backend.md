# GraphQL backend for a fintech mobile app

*Fintech client · 2024-05 · 4 weeks · hourly*

The app made many slow REST calls per screen. I was brought in to fix that.

I started by working out what good looked like with the client, then designed a GraphQL schema around the app's screens. Next I added batching and caching to remove repeated queries. Finally, I set up auth with refresh tokens.

The result: I cut API calls per screen from 8 to 1, and brought p95 API latency under 159 ms.

Tools: GraphQL, Node.js, TypeScript.
