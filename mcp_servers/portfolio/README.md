# GigBid Portfolio MCP server

A read-only MCP server that lets an AI assistant (Claude Desktop, Cursor, or the GigBid agents)
find **proof** in a freelancer's portfolio. That means CVs, case studies, repo READMEs and code,
client reviews, and past proposals with their outcomes. It's built with the official MCP Python SDK
(v2).

## What it offers

| Tool | Use it to |
|---|---|
| `search_evidence(requirement, k, filters)` | Find the documents that prove one job requirement. Each result has its type, source, date, outcome (for past proposals) and the passage that matches. |
| `get_case_study(id)` | Read a case study in full, with links to the README, code and reviews of the same project |
| `list_skills()` | See every skill the portfolio proves, and whether it is named or only shown in code |

**Resources:** `portfolio://case/{id}`, `portfolio://proposal/{id}`, `portfolio://doc/{id}`, `portfolio://cv`.

**Prompts:**
- "Answer a screening question from my portfolio"
- "Prove a requirement"
- "Outline a proposal for a job post"

The prompts tell the model to cite evidence ids, and to say so when there is no evidence rather than
invent experience.

## Run it

From the GigBid repository root:

```bash
pip install -e .
PORTFOLIO_PERSONA=ai_ml python -m mcp_servers.portfolio.server                    # stdio
PORTFOLIO_USERS="secret-token=me:ai_ml" python -m mcp_servers.portfolio.server --http --port 8201
```

To use it from Claude Desktop, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gigbid-portfolio": {
      "command": "/path/to/gigbid/.venv/bin/python",
      "args": ["-m", "mcp_servers.portfolio.server"],
      "cwd": "/path/to/gigbid",
      "env": { "PORTFOLIO_PERSONA": "ai_ml" }
    }
  }
}
```

## Use your own portfolio

Put your files in `data/personas/<your_name>/`, in the same layout as the bundled personas:

- `cv.md`
- `case_studies/*.md`
- `repos/<repo>/README.md` and code files
- `reviews.jsonl`
- `proposals.jsonl`
- `evidence_index.jsonl`, with one line per document: id, type, title, path, skills, date and, for proposals, the outcome

Then set `PORTFOLIO_PERSONA=<your_name>`.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PORTFOLIO_PERSONA` | Portfolio for the stdio user | `ai_ml` |
| `PORTFOLIO_USERS` | HTTP users: `token=user_id:persona,...` | a dev token |
| `PORTFOLIO_RETRIEVER` | Search variant (see `rag/retriever.py`) | `bm25` |

## Safety

- **Read-only.** Every tool is annotated `readOnlyHint`.
- **Tenant-scoped.** Over HTTP, each bearer token is tied to one portfolio, and a user can never search another user's documents.
- **No proof from pitches.** Past proposals are returned as evidence of what was *pitched*. `list_skills` does not count them as proof of a skill.
