# What I learned from a fintech project

Back in 2026 a fintech client came to me because data from several partner systems was copied across by hand every day. The interesting part was not the model or the code. It was agreeing what to measure. Once we had that, I designed a single integration service with retries and idempotency and the rest followed.

Numbers, for the record: removed about 11 hours of manual copying per week; kept sync failures below 2%.
