#!/usr/bin/env bash
# Deploy the committed code to a Hugging Face Space (Docker SDK, free CPU: 2 vCPU, 16 GB RAM).
#
#   HF_TOKEN=hf_... infra/deploy/hf_space.sh <hf-user>/<space-name>
#   infra/deploy/hf_space.sh <hf-user>/<space-name> --dry-run      # only build the folder and show it
#   infra/deploy/hf_space.sh <hf-user>/<space-name> --gradio       # a Space created with the Gradio SDK
#
# --gradio: for accounts where the Docker SDK isn't free. The Space runs app.py
# (infra/deploy/space_app.py), which starts the same stack without Redis or the worker
# (GIGBID_RUNNER=inprocess). The web app is built here and pushed with the code, because that
# SDK has no Node.js; requirements.txt comes from pyproject.toml.
#
# What it does: exports the current commit (git archive: committed files only, so no .env and no
# data/processed), adds the Dockerfile at the root with the single-container command
# (infra/docker/start-all.sh: Redis, GigSim, worker, API + web on port 7860) and a README with
# the Space settings, then force-pushes that snapshot to the Space. Your GitHub history is not
# touched. Set the secrets in the Space's settings, not here (see docs/RUNBOOK.md, "Deploy").
set -euo pipefail

SPACE="${1:?usage: hf_space.sh <hf-user>/<space-name> [--dry-run] [--gradio]}"
shift
DRY="" GRADIO=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY="--dry-run" ;;
    --gradio) GRADIO=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
ROOT="$(git rev-parse --show-toplevel)"
SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "note: uncommitted changes are not deployed (only commit $SHA is)" >&2
fi

OUT="$(mktemp -d)/space"
mkdir -p "$OUT"
git -C "$ROOT" archive HEAD | tar -x -C "$OUT"

if [[ -n "$GRADIO" ]]; then
  (cd "$ROOT/apps/web" && npm ci --no-audit --no-fund --silent && npm run build --silent)
  mkdir -p "$OUT/apps/web" && cp -r "$ROOT/apps/web/dist" "$OUT/apps/web/dist"
  cp "$ROOT/infra/deploy/space_app.py" "$OUT/app.py"
  # The SDK installs gradio[oauth,mcp] in the same pip call as requirements.txt, and gradio 6.x's
  # mcp extra pins mcp<2 and pydantic<=2.12.5, while GigBid needs mcp 2.x and pydantic 2.14. Those
  # packages go to requirements-runtime.txt instead, which app.py installs at start-up (nothing on
  # the Space imports gradio, so breaking its mcp extra doesn't matter).
  RUNTIME_ONLY="mcp pydantic"
  python3 - "$ROOT/pyproject.toml" "$OUT" $RUNTIME_ONLY <<'PY'
import re, sys, tomllib
pyproject, out, *runtime_only = sys.argv[1:]
deps = tomllib.load(open(pyproject, "rb"))["project"]["dependencies"]
name = lambda r: re.match(r"[A-Za-z0-9_.-]+", r).group(0).lower()
open(f"{out}/requirements.txt", "w").write("".join(f"{r}\n" for r in deps if name(r) not in runtime_only))
open(f"{out}/requirements-runtime.txt", "w").write("".join(f"{r}\n" for r in deps if name(r) in runtime_only))
PY
  SDK_YAML='sdk: gradio
sdk_version: 6.32.0
python_version: "3.12"
app_file: app.py'
else
  sed -e 's|^EXPOSE 8000|EXPOSE 7860|' \
      -e 's|^CMD .*|CMD ["infra/docker/start-all.sh"]|' \
      "$ROOT/infra/docker/Dockerfile" > "$OUT/Dockerfile"
  SDK_YAML='sdk: docker'
fi

cat > "$OUT/README.md" <<EOF
---
title: GigBid
emoji: 📝
colorFrom: indigo
colorTo: blue
$SDK_YAML
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
  head -n 14 "$OUT/README.md"
  if [[ -n "$GRADIO" ]]; then ls "$OUT/app.py" "$OUT/requirements.txt" "$OUT/apps/web/dist/index.html"
  else grep -n "^EXPOSE\|^CMD" "$OUT/Dockerfile"; fi
  exit 0
fi

: "${HF_TOKEN:?set HF_TOKEN to a Hugging Face token with write access}"
cd "$OUT"
git init -q -b main
git add -A
# apps/web/.gitignore ignores dist, but the Gradio Space needs the prebuilt web app
[[ -n "$GRADIO" ]] && git add -f apps/web/dist
git -c user.name="${GIT_AUTHOR_NAME:-$(git -C "$ROOT" config user.name)}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-$(git -C "$ROOT" config user.email)}" \
    commit -q -m "Deploy GigBid $SHA"
AUTH="$(printf 'user:%s' "$HF_TOKEN" | base64 -w0)"
git -c http.extraHeader="Authorization: Basic $AUTH" push --force -q "https://huggingface.co/spaces/$SPACE" main
echo "Pushed. Build log and app: https://huggingface.co/spaces/$SPACE"
