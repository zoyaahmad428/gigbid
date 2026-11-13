# call-notes-media

Meeting and call summarisation for a media sales team.

## What it does
Reps wrote call notes by hand and CRM records were thin. This is the code from when I prompted an LLM to produce structured notes and next steps.

## Stack
transcription and summary worker; Python, LLM, NLP.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: saved reps about 15 minutes per call.
