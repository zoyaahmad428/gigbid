# Hidden simulator data. Agents must never read this folder.

- `clients_hidden.jsonl`: each client's hidden preferences (must-have skills, budget ceiling,
  dislike of generic openings, responsiveness, scammer flag). Only the simulated client reads these.
- `live_labels.jsonl`: ground-truth labels for the live stream (duplicate of, scam pattern,
  injection type and location). Used only by evaluation runners.

The Portfolio and Marketplace MCP servers must not expose anything from this folder.
See `docs/DATA_CARD.md`.
