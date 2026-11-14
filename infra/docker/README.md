# Docker

One image (`Dockerfile`) for every service; the command picks the role. Build it from the repository
root: `make image`, or let `make up` build it.

- **Build stages.** A Node stage builds the web app. The Python stage installs the pinned
  dependencies, downloads the deployed retriever's two models into `/opt/models` (so the first
  request doesn't wait for them), copies the code and runs as UID 1000.
- **Roles.** `../docker-compose.yml` runs Postgres, Redis, GigSim, `gigsim-init`, the API (which
  serves the web app) and the Arq worker; `--profile mcp` adds the three MCP servers.
- **`start-all.sh`** runs the whole stack in one container for a host with a single public port
  (see the Deploy section of `docs/RUNBOOK.md`). If any process exits, the container exits, so the
  platform restarts it.
- **`gigsim_warm.py`** moves a fresh GigSim's clock forward so a new deployment has open jobs.

CI builds the image on every push, checks the compose file, and smoke-tests the single
container: health, the web app, a seeded login, jobs and Connects.
