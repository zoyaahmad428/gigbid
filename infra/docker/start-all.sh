#!/usr/bin/env bash
# The whole stack in one container, for a demo deployment with a single public port
# (Hugging Face Spaces, Render, a small VM): Redis, GigSim, the worker and the API (which serves
# the web app). Only the API's port is public; GigSim and Redis listen on 127.0.0.1.
#
# State lives under data/processed (SQLite checkpoints, app store, traces, the Redis AOF) unless
# DATABASE_URL points at a Postgres (e.g. a free Neon/Supabase database), which makes checkpoints
# and memory survive a redeploy. If any process exits, the container exits so the platform
# restarts it, instead of running with a piece missing.
set -euo pipefail
cd "$(dirname "$0")/../.."                 # the repository root (/app in the image)

PORT="${PORT:-7860}"
GIGSIM_PORT="${GIGSIM_PORT:-8081}"
STATE_DIR="${GIGBID_STATE_DIR:-data/processed}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export GIGSIM_BASE_URL="${GIGSIM_BASE_URL:-http://127.0.0.1:$GIGSIM_PORT}"
export GIGBID_RUNNER="${GIGBID_RUNNER:-worker}"
export GIGSIM_STATE_DB="${GIGSIM_STATE_DB:-$STATE_DIR/gigsim.sqlite3}"

mkdir -p "$STATE_DIR/redis"
if [[ "$REDIS_URL" == redis://127.0.0.1* ]]; then
  REDIS_PORT="${REDIS_URL##*:}"; REDIS_PORT="${REDIS_PORT%%/*}"
  redis-server --bind 127.0.0.1 --port "$REDIS_PORT" --dir "$STATE_DIR/redis" --appendonly yes --save "" &
fi
uvicorn gigsim.app:app --host 127.0.0.1 --port "$GIGSIM_PORT" --log-level warning &

GIGSIM_BASE_URL="$GIGSIM_BASE_URL" python - <<'EOF'
import os, time, urllib.request
for _ in range(120):
    try:
        urllib.request.urlopen(os.environ["GIGSIM_BASE_URL"] + "/health", timeout=2)
        break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit("GigSim did not start")
EOF
python infra/docker/gigsim_warm.py                # a fresh simulator gets ~3 days of open jobs

arq worker.main.WorkerSettings &
uvicorn apps.api.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers &

wait -n
echo "a GigBid process exited; stopping the container so it is restarted" >&2
exit 1
