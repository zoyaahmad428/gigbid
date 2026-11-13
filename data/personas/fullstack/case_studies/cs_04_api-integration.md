# Third-party API integration layer for a edtech platform

*Edtech client · 2024-02 · 11 weeks · hourly*

Data from several partner systems was copied across by hand every day. I was brought in to fix that.

I started by working out what good looked like with the client, then designed a single integration service with retries and idempotency. Next I normalised partner data into one schema. Finally, I added monitoring for failed syncs.

The result: I removed about 29 hours of manual copying per week, and kept sync failures below 1%.

Tools: Node.js, REST API, TypeScript, PostgreSQL.
