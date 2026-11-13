# What I learned from a hospitality project

Back in 2025 a hospitality client came to me because models were retrained by hand on a laptop and nobody noticed drift. The interesting part was not the model or the code. It was agreeing what to measure. Once we had that, I moved training into a reproducible pipeline with experiment tracking and the rest followed.

Numbers, for the record: cut release time for a new model from 4 days to 2; caught 4 data-quality incidents before they reached users.
