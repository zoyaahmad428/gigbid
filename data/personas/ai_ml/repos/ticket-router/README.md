# ticket-router

Ticket triage classifier for a logistics help desk.

## What it does
Incoming tickets were routed by hand and often reached the wrong team. This is the code from when I fine-tuned a small transformer and compared it against a TF-IDF baseline.

## Stack
training script, evaluation notebook, batch scorer; Python, NLP, PyTorch, scikit-learn.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: routed 65% of tickets automatically.
