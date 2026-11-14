# GigBid web app

React 19 + TypeScript + Vite. The only backend is `apps/api` (FastAPI).

```bash
make gigsim          # terminal 1: the marketplace simulator on :8081
make api             # terminal 2: the API on :8000 (needs GIGBID_DB=sqlite in .env for local runs)
make web-install && make web   # terminal 3: http://localhost:5173
```

Create an account, then onboarding asks for a portfolio (one of the three personas), your rates,
the autonomy setting and the marketplace token (pre-filled with that persona's GigSim dev token).

## Screens

| Route | Screen | What it does |
|---|---|---|
| `/login` | Sign in / create account | Email + password; the server keeps only a hash of the session token |
| `/onboarding` | Onboarding | Portfolio, rates, autonomy, marketplace token (checked against the marketplace) |
| `/` | Job inbox | Open jobs, filtered to your category by default; skills you have are highlighted; "Analyse" starts a bid run |
| `/jobs/:id` | Job detail | The post (marked as untrusted client text), client history, run status, "bid anyway" after a skip |
| `/approvals` | Approvals | Everything waiting for you, rebuilt from the server on every load |
| `/runs/:thread` | Proposal studio + run viewer | Tabs: **Proposal** (claim-highlighted letter, evidence on click, fit matrix, risk, price, dry-run preview, critic rounds, approve / edit / reject), **Agent run** (live steps over SSE, cost per node, checkpoints, retry), **Versions** (every draft), **Trace** (every LLM and tool call) |
| `/pipeline` | Pipeline | Kanban from analysing to hired; marketplace status wins once a proposal exists |
| `/messages` | Messages | Client replies per proposal; "Draft a reply" runs the conversation graph, which stops for approval |
| `/analytics` | Analytics | Approval rate, response rate, Connects, LLM cost, fit distribution, skip reasons (all measured) |
| `/settings` | Settings | Profile, rates, autonomy, token; the memory inspector (delete learned style rules and outcome lessons); agent thresholds (read only) |

## How approval works

Starting, approving, editing and rejecting all return immediately; the graph runs as a background
task on the API and every event it yields is stored. `GET /api/runs/{id}/stream` (server-sent events)
replays the stored events and then follows new ones, so a refresh or a second tab rebuilds the same
timeline. The pending approval itself is in the LangGraph checkpointer, so it survives a restart of
the API; runs that were mid-flight when the API stopped are marked `interrupted` and can be retried
from their last checkpoint.

"Approve and send" opens a confirmation that shows the bid and the Connects it spends (from the
marketplace's dry run); nothing is sent until the second click. Edits go back through the Claim
Verifier and Compliance Guard before they return for approval.

## Tests

`make web-test` runs Playwright against the dev server with the API mocked from a real captured
pending-approval payload (`tests/fixtures/run_pending.json`): the approval survives a reload, the
confirmation shows the Connects cost before sending, reject needs a reason, edits send the new text
and bid, citations open the evidence, and the page fits a 390 px phone.
