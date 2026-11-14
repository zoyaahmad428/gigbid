# Worker

Arq on Redis: long-running graph runs and the schedules. `make worker` locally, or the `worker`
service in docker compose. The API hands runs to it when started with `GIGBID_RUNNER=worker`.

| Job | When | What it does |
|---|---|---|
| `run_thread` | queued by the API, the scout or the monitor | Runs one operation on a graph thread (`start_bid`, `start_conversation`, `resume`, `retry`) through `Runtime.execute`, writing every event to the app store so the browser's live stream shows it |
| `scout_all` → `scout_user` | every 30 min (`worker.scout_every_minutes`) | For users who turned on auto-scout: the newest jobs in their category without a run; starts at most 2 bid runs per tick |
| `monitor_all` → `monitor_user` | every 5 min, and at start-up | Reads marketplace events after a saved cursor: a client message starts a reply draft; resolved proposals feed the outcome memory |
| `recover` | at start-up | Re-queues runs left queued/running with no job in the queue |

Nothing here can send. Bid and reply runs stop at the approval interrupt, and only the API resumes
one, with the human's decision.

## Safe to repeat

Arq retries a job after a crash, and a scheduled tick can fire twice. Each layer makes a repeat
harmless:

- **Continue, never restart.** `Runtime.execute` carries on from a thread's last checkpoint if a
  run died half-way. Anything else it does only if the thread is still at the checkpoint the
  caller saw.
- **Job ids.** Each id is derived from the thread, the operation and that checkpoint, so the same
  request queued twice runs once.
- **Thread lock.** A Redis lock per thread stops two workers driving one thread.
- **Event cursor.** The monitor saves its cursor only after the events were handled.
- **Submit.** The submit node's idempotency keys make sending exactly-once on top of all this.

Only the user id goes on the queue; the worker re-reads the user's settings (and marketplace
token) from the app store.

Tests: `tests/integration/test_worker.py`. They cover crash and resume, repeated delivery and
"bid anyway" on SQLite and Postgres, scouting, the monitor, and a full Arq round trip on Redis
(set `GIGBID_TEST_POSTGRES_URL` and `GIGBID_TEST_REDIS_URL`; CI does).
