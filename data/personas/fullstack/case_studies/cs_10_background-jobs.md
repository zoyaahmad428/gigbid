# Background job system for a B2B SaaS reporting product

*B2b saas client · 2025-07 · 5 weeks · fixed-price*

Large reports timed out inside web requests. I was brought in to fix that.

I started by working out what good looked like with the client, then moved report generation to background workers. Next I added progress updates to the UI. Finally, I made jobs idempotent so retries never duplicated work.

The result: I removed all report timeouts (49 per week before), and cut the slowest report from 11 minutes to 2.

Tools: Django, REST API, React.
