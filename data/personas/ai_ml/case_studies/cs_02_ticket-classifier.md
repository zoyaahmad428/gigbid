# Ticket triage classifier for a logistics help desk

**Client:** logistics company  
**When:** 2023-08 to 2023-11  
**Engagement:** fixed-price

## Situation
Incoming tickets were routed by hand and often reached the wrong team.

## What I did
- Labelled a sample of historical tickets with the support lead.
- Fine-tuned a small transformer and compared it against a TF-IDF baseline.
- Added a confidence threshold so low-confidence tickets still go to a person.

## Outcome
- Routed 65% of tickets automatically.
- Raised macro-F1 from 0.62 to 0.86.

## Stack
Python · NLP · PyTorch · scikit-learn
