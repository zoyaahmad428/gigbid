# Runbook

How to start GigBid, deploy it, and what to do when something goes wrong.

## Ways to run it

| | What runs | When to use it |
|---|---|---|
| Local dev | `make gigsim`, `make api`, `make web` (three terminals). Runs execute inside the API; SQLite for everything. | Working on code. Needs `GIGBID_DB=sqlite` in `.env`. |
| Docker compose | `make up`: Postgres, Redis, GigSim, API (serves the web app), Arq worker. | The full system on one machine, as it is deployed. |
| Single container | `infra/docker/start-all.sh` inside the image: Redis, GigSim, worker and API in one container. | A demo host with one public port (Hugging Face Spaces). |

The Groq key goes in `.env` as `OPENAI_API_KEY` (never in Git). Every other setting has a default
in `agent/config.yaml` or `.env.example`.

### Docker compose

```bash
cp .env.example .env            # then set OPENAI_API_KEY; GIGBID_DB and GIGBID_RUNNER are overridden by compose
make up                         # builds the image (first time: ~5 min, includes the retrieval models)
open http://localhost:8000      # sign up, pick a portfolio, use token dev-token-ai-ml (or -fullstack, -data-analyst)
make logs                       # API and worker logs
make down                       # stop; data stays in the docker volumes
```

`docker compose -f infra/docker-compose.yml --profile mcp up -d` also starts the three MCP servers
on ports 8201–8203 (Streamable HTTP, bearer auth) for MCP Inspector.

A fresh GigSim starts its clock where the live job stream starts, so `gigsim-init` moves a new
simulator 72 simulated hours forward (about 110 open jobs). Set `GIGSIM_WARM_START_HOURS=0` to
skip that.

## Deploy (Hugging Face Spaces)

The free CPU Space (2 vCPU, 16 GB RAM) runs the single-container image. The steps:

1. **Create the Space.** Create a Hugging Face account and a new Space: SDK **Docker**, template
   *Blank*, visibility **Public** (reviewers must open it without a Hugging Face login; the app has
   its own sign-in).
2. **Set the secrets and variables.** In the Space settings → *Variables and secrets*:

   | Name | Kind | Value |
   |---|---|---|
   | `OPENAI_API_KEY` | secret | the Groq key |
   | `GIGBID_SEED_USERS` | secret | reviewer accounts, `email:password:persona` separated by commas, e.g. `reviewer@example.com:<password>:ai_ml` |
   | `GIGBID_ALLOW_SIGNUP` | variable | `false` (only the seeded accounts can use the Groq key) |
   | `DATABASE_URL` | secret, optional | a Postgres URL (e.g. a free Neon database). Checkpoints and memory then survive restarts. |
   | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | secret, optional | send traces to Langfuse Cloud |

3. **Push the code.** Commit everything, then run
   `HF_TOKEN=hf_... infra/deploy/hf_space.sh <hf-user>/<space-name>`. Use a token with write access,
   and run `make deploy-dry` first to see what would be pushed. The script pushes a snapshot of the
   current commit with a root `Dockerfile`; your GitHub history is not touched.
4. **Check the deployment.** Wait for the build (about 10 minutes). Then sign in with a seeded
   account, open the Inbox and analyse a job. Give reviewers the URL and their account.

**Limits of the free Space:**
- **Persistence.** The disk is not persistent. A restart (after 48 h without visitors, or a
  redeploy) clears runs and users, and seeded accounts are recreated at start-up. With
  `DATABASE_URL` set, thread checkpoints and the memory store are kept in Postgres.
- **Cold start.** The Space sleeps when unused, and the first visit wakes it (about 1 minute).

## Configuration that matters in operation

| Setting | Where | Default | What it does |
|---|---|---|---|
| `GIGBID_RUNNER` | env | `inprocess` | `worker`: the API queues runs for the Arq worker (compose and deployment) |
| `budgets.max_runs_per_user_per_day` | config.yaml | 25 | New bid and reply runs per user per day (cost guard); returns 429 above it |
| `budgets.max_tokens_per_job`, `max_graph_steps` | config.yaml | 40k, 30 | Per-thread limits; exceeding moves the thread to `needs_attention` |
| `worker.scout_every_minutes` | config.yaml | 30 | Scouting for users who turned on auto-scout (Settings) |
| `worker.scout_max_new_runs` | config.yaml | 2 | Bid runs the scout starts per user per tick |
| `worker.monitor_every_minutes` | config.yaml | 5 | Reading marketplace events (client messages, outcomes) |
| `worker.job_timeout_seconds` | config.yaml | 600 | A run's time limit; also when a hard-killed worker's job runs again |
| `GIGBID_WORKER_JOBS` | env | 2 | Runs a worker executes at once (Groq free tier: keep it low) |

## Rate limits

- **GigSim:** 60 requests per minute per token, burst 20. The adapter retries `429` and `5xx` with
  backoff; the monitor caches job and market lookups so a backlog of events doesn't hit the limit.
- **Groq free tier:** the OpenAI client retries rate-limited calls (`Retrying request in 12 s` in
  the worker log is normal). Two runs at a time is the practical limit.
- **Marketplace policy:** at most 30 submissions per simulated day and 80 Connects per week
  (`gigsim/policy.yaml`); the Pricing node checks the balance before drafting.

## Recovery

GigBid never sends anything without a human approving it, so recovery is about not losing work
and not doing anything twice.

| Situation | What happens | What to do |
|---|---|---|
| API restarts while a proposal waits for approval | The approval is in the checkpointer; the page shows it again after reload | Nothing |
| API restarts during a run (local dev, in-process runs) | The run is marked `interrupted` at start-up | Press **Retry**: it continues from the last checkpoint |
| Worker stopped normally (`docker compose stop`, SIGTERM) | Arq cancels the job and puts it back in the queue; the next worker continues the thread from its last checkpoint | Nothing (tested: stopped after `pricing`, the restarted worker continued at `writer`; the analysis ran once) |
| Worker killed (`kill -9`, out of memory) | The job runs again once `worker.job_timeout_seconds` has passed | Wait, or lower the timeout |
| Redis lost its data | At start-up the worker re-queues runs still marked queued/running that have no job in the queue | Nothing |
| The same request queued twice (double click, a scheduled tick twice) | Job ids come from what the job does, so it runs once; a Redis lock stops two workers driving one thread | Nothing |
| A job is run again after it had already finished | `Runtime.execute` sees the thread is already past that point and only reports the result | Nothing |
| Approval resumed twice, or a crash between submit and checkpoint | The submit node's idempotency keys come from the thread id; the marketplace returns the first result | Nothing; Connects are spent once |

### Degraded mode

If GigSim (or the live marketplace) is unreachable after retries, the thread stops with status
`degraded` and the reason "marketplace unavailable"; nothing is half-sent. When the marketplace
is back, press **Retry** on the run: it re-runs from the checkpoint before the node that failed.
Model-provider failures follow the same path: the cheap nodes fall back to a second model first, and
a node that still fails moves the thread to `dead_letter` with the error kept for the run viewer.

## Observability

- **Run viewer (UI):** every step, LLM call and tool call of a thread, with tokens, cost and
  latency, from the trace store (`data/processed/traces.sqlite3`).
- **Langfuse:** with `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set, the same events go to
  Langfuse. There is one trace per thread (session = thread id, user = GigBid user id); LLM calls
  are generations with token usage and cost, and tool calls are spans. Inputs are PII-masked before
  they leave. Export runs on a background thread, and an outage drops events without affecting runs.
- **Logs:** `make logs`. The worker logs one line per job (`→` start, `←` result,
  `↻ cancelled, will be run again`).

## Data and backups

| Data | Where | Back up with |
|---|---|---|
| Thread checkpoints, memory store | Postgres (`pgdata` volume) | `docker compose exec postgres pg_dump -U gigbid gigbid > backup.sql` |
| Users, sessions, run index, run events, traces | SQLite in the `appdata` volume (`/app/data/processed`), shared by API and worker | `docker run --rm -v gigbid_appdata:/d -v $PWD:/b alpine tar czf /b/appdata.tgz -C /d .` |
| GigSim state | `gigsimstate` volume | the same, with `gigbid_gigsimstate` |

The app store and traces stay in SQLite (WAL mode; the API and the worker share the file on one
host). Moving them to Postgres is the change needed before running more than one host.
