# Model deployment and monitoring setup for a healthcare team

**Client:** healthcare company  
**When:** 2024-03 to 2024-05  
**Engagement:** hourly

## Situation
Models were retrained by hand on a laptop and nobody noticed drift.

## What I did
- Moved training into a reproducible pipeline with experiment tracking.
- Served models behind an API with versioned releases.
- Added drift and data-quality alerts.

## Outcome
- Cut release time for a new model from 3 days to 2.
- Caught 7 data-quality incidents before they reached users.

## Stack
Python · MLOps · Docker · AWS
