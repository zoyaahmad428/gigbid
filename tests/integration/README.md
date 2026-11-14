# Integration tests

| File | What it covers | Needs |
|---|---|---|
| `test_api.py` | Auth and onboarding gate; private runs and validated decisions; SSE replay; orphaned runs; queueing to the worker (user id only on the queue); closed sign-up, seeded reviewer accounts and the daily run cap | nothing (fake marketplace) |
| `test_worker.py` | A crashed run continues from its checkpoint; a job delivered twice does nothing; "bid anyway" restarts only from the checkpoint the user saw; scouting; the event monitor; the thread lock | nothing; also runs on Postgres with `GIGBID_TEST_POSTGRES_URL` |
| `test_worker.py::test_arq_round_trip…` | A run queued through the API's run manager is executed by a real Arq worker | `GIGBID_TEST_REDIS_URL` |

GigSim's own tests (`gigsim/tests`) and the MCP server tests (`mcp_servers/*/tests`) run GigSim
in-process. The end-to-end runs against GigSim with a real model (`make demo`, the evaluations)
need the Groq key and are not part of `pytest`.
