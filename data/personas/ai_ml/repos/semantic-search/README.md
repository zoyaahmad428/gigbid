# semantic-search

Semantic product search for a manufacturing catalogue.

## What it does
Keyword search failed on synonyms and misspellings, so users left. This is the code from when I blended semantic and keyword scores and tuned the weights on click logs.

## Stack
embedding job and ranking service; Python, NLP, Vector Database.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: raised search click-through by 14%.
