# MCP tool inventory

GigBid's agents reach every capability through MCP tools. There are three servers we wrote, plus
third-party servers. The tool schemas are pinned by `tests/contract/snapshots/*.json`: CI fails if a
signature changes without the snapshot being updated.

**Annotations key:**

| Letter | Hint |
|---|---|
| R | `readOnlyHint` |
| D | `destructiveHint` |
| I | `idempotentHint` |
| O | `openWorldHint` |

A dash means false.

**Auth:**

| Transport | How the user is identified |
|---|---|
| HTTP (Streamable HTTP) | `Authorization: Bearer <token>`. Each token maps to one user; every query is scoped to that user's data. |
| stdio | One local user, set by environment variables |

## Portfolio server (`mcp_servers/portfolio`, read-only)

| Tool | Read/write | R D I O | Auth | Purpose |
|---|---|---|---|---|
| `search_evidence(requirement, k, filters)` | read | R - I - | bearer / local | Evidence for one requirement. Returns type, source, date, outcome and a snippet. Filters: types, exclude_types, project, outcomes, skills, dates. |
| `get_case_study(id)` | read | R - I - | bearer / local | Full case study by evidence id or project id, plus related docs |
| `list_skills()` | read | R - I - | bearer / local | Skills with project and document counts, named or implied, last used |

**Resources:**
- `portfolio://case/{case_id}`
- `portfolio://proposal/{proposal_id}`
- `portfolio://doc/{evidence_id}`
- `portfolio://cv`

**Prompts:**
- `answer_screening_question`
- `prove_requirement`
- `outline_proposal`

## Marketplace server (`mcp_servers/marketplace`, wraps the marketplace port)

| Tool | Read/write | R D I O | Auth | Notes |
|---|---|---|---|---|
| `marketplace_status()` | read | R - I O | bearer / local | Reachability, mode, policy version. Drives the degraded-mode banner. |
| `search_jobs(query, category, skills, budget_type, min_budget, posted_after, status, limit, cursor)` | read | R - I O | bearer / local | Newest first |
| `get_job(job_id)` | read | R - I O | bearer / local | |
| `get_client_history(client_id)` | read | R - I O | bearer / local | Public record only; hidden preferences are never exposed |
| `get_connects_balance()` | read | R - I O | bearer / local | |
| `get_market_stats(category)` | read | R - I O | bearer / local | Price percentiles |
| `list_proposals(status)` | read | R - I O | bearer / local | |
| `list_messages(proposal_id)` | read | R - I O | bearer / local | Client text is untrusted |
| `get_events(since)` | read | R - I O | bearer / local | Pipeline board updates |
| `create_proposal_draft(job_id, cover_letter, bid_amount, bid_type, idempotency_key, screening_answers, dry_run=false)` | write | - - I O | bearer / local | Draft only: nothing sent, no Connects spent |
| `submit_proposal(proposal_id, idempotency_key, dry_run=true)` | write | - D I O | bearer / local | **Spends Connects.** Dry run by default. |
| `send_message(proposal_id, text, idempotency_key, dry_run=true)` | write | - - I O | bearer / local | Dry run by default |

**Write-tool rules:**
- **Dry run first.** `submit_proposal` and `send_message` default to `dry_run=true` and return exactly what would be sent, plus the cost and rule checks. GigBid shows that preview on the approval screen. It calls `dry_run=false` only after the human approves.
- **Idempotency.** Every write needs an `idempotency_key`. The server stores the result per (user, tool, key) and passes the key down to GigSim, so a retried call returns the first result and never submits twice or spends Connects twice.
- **Rate limit.** 40 requests a minute per user (burst 10), below GigSim's 60.
- **Retries.** The adapter retries 429, 5xx and timeouts with backoff, at most 3 times.
- **Audit log.** `mcp_servers/marketplace/audit/audit.jsonl` is append-only and hash-chained. Each line holds the user, tool, masked arguments, result and trace id.

## Workspace server (`mcp_servers/workspace`, local mock)

| Tool | Read/write | R D I O | Auth | Notes |
|---|---|---|---|---|
| `get_free_slots(days, duration_minutes, max_slots, timezone, client_timezone, start_date)` | read | R - I - | bearer / local | Mon–Fri 09:00–17:00, avoids meetings and holds |
| `create_tentative_hold(start, duration_minutes, title, idempotency_key, note)` | write | - - I - | bearer / local | Own calendar only; no invite is sent |
| `list_holds()` | read | R - I - | bearer / local | |
| `cancel_hold(hold_id)` | write | - D I - | bearer / local | |
| `create_email_draft(to_label, subject, body, idempotency_key)` | write | - - I - | bearer / local | Never sends |

This server is backed by SQLite, not Google Calendar or Gmail, so the demo needs no real accounts.
Swapping in the Google APIs would change only `mcp_servers/workspace/server.py`.

## Third-party servers

| Server | Used for | Trust rationale |
|---|---|---|
| Upwork official MCP (`https://mcp.upwork.com/mcp`) | Live mode, behind `UpworkLiveAdapter` (**not implemented yet**) | Run by the platform itself, behind OAuth 2.1; every write is a draft the user confirms on Upwork |
| GitHub MCP server | Repo ingestion for the portfolio (planned, RAG step) | Maintained by GitHub; read-only token scoped to public repos |

## Which agent gets which tools

Agents get tools by name through allowlists in `agent/mcp_client.py`, filled in the agent step.

| Agent | Tools |
|---|---|
| Scout | `search_jobs`, `get_job`, `marketplace_status` |
| Risk Assessor | `get_job`, `get_client_history` |
| Fit Matcher, Proposal Writer, Critic | `search_evidence`, `get_case_study`, `list_skills` |
| Pricing Strategist | `get_market_stats`, `get_connects_balance`, `get_client_history` |
| Client Liaison | `list_messages`, `send_message` (after approval), `get_free_slots`, `create_tentative_hold`, `create_email_draft` |
| Approval executor (no LLM) | `create_proposal_draft`, `submit_proposal` |
