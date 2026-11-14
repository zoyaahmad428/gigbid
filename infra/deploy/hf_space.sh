#!/usr/bin/env bash
# Deploy the committed code to a Hugging Face Space (Docker SDK, free CPU: 2 vCPU, 16 GB RAM).
#
#   HF_TOKEN=hf_... infra/deploy/hf_space.sh <hf-user>/<space-name>
#   infra/deploy/hf_space.sh <hf-user>/<space-name> --dry-run      # only build the folder and show it
#
# What it does: exports the current commit (git archive: committed files only, so no .env and no
# data/processed), adds the Dockerfile at the root with the single-container command
# (infra/docker/start-all.sh: Redis, GigSim, worker, API + web on port 7860) and a README with
# the Space settings, then force-pushes that snapshot to the Space. Your GitHub history is not
# touched. Set the secrets in the Space's settings, not here (see docs/RUNBOOK.md, "Deploy").
set -euo pipefail

SPACE="${1:?usage: hf_space.sh <hf-user>/<space-name> [--dry-run]}"
DRY="${2:-}"
ROOT="$(git rev-parse --show-toplevel)"
SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "note: uncommitted changes are not deployed (only commit $SHA is)" >&2
fi

OUT="$(mktemp -d)/space"
mkdir -p "$OUT"
git -C "$ROOT" archive HEAD | tar -x -C "$OUT"

sed -e 's|^EXPOSE 8000|EXPOSE 7860|' \
    -e 's|^CMD .*|CMD ["infra/docker/start-all.sh"]|' \
    "$ROOT/infra/docker/Dockerfile" > "$OUT/Dockerfile"

cat > "$OUT/README.md" <<EOF
---
title: GigBid
emoji: 📝
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Multi-agent freelance proposal assistant (AI-4016)
---

# GigBid (demo deployment)

GigBid reads freelance jobs on GigSim (a simulated marketplace), decides whether to bid, drafts
cited proposals and client replies, and **never sends anything without a human approving it**.

This Space runs the whole stack in one container: the web app and API, the Arq worker, Redis
and GigSim. Source, documentation and evaluation results: see the GitHub repository.
Deployed from commit $SHA.

Sign in with the reviewer account you were given. Sign-up is closed on this deployment.
EOF

echo "Space folder: $OUT ($(find "$OUT" -type f | wc -l) files, commit $SHA)"
if [[ "$DRY" == "--dry-run" ]]; then
  head -n 12 "$OUT/README.md"
  grep -n "^EXPOSE\|^CMD" "$OUT/Dockerfile"
  exit 0
fi

: "${HF_TOKEN:?set HF_TOKEN to a Hugging Face token with write access}"
cd "$OUT"
git init -q -b main
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-$(git -C "$ROOT" config user.name)}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-$(git -C "$ROOT" config user.email)}" \
    commit -q -m "Deploy GigBid $SHA"
AUTH="$(printf 'user:%s' "$HF_TOKEN" | base64 -w0)"
git -c http.extraHeader="Authorization: Basic $AUTH" push --force -q "https://huggingface.co/spaces/$SPACE" main
echo "Pushed. Build log and app: https://huggingface.co/spaces/$SPACE"
