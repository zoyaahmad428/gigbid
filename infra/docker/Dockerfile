# One image for every GigBid service; the command picks the role (see infra/docker-compose.yml):
#   api      uvicorn apps.api.main:app        (also serves the built web app)
#   worker   arq worker.main.WorkerSettings
#   gigsim   uvicorn gigsim.app:app
#   mcp-*    python -m mcp_servers.<name>.server --http
#   all      infra/docker/start-all.sh        (single container for a demo deployment)
#
# Build from the repository root:  docker build -f infra/docker/Dockerfile -t gigbid .

# ---- web app (React + Vite) ----------------------------------------------------------
FROM node:22.12.0-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

# ---- Python services -----------------------------------------------------------------
FROM python:3.12.8-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app RAG_MODEL_CACHE=/opt/models GIGBID_DB=auto
WORKDIR /app

# Only the all-in-one demo container uses redis-server; the compose stack has its own Redis.
# The app runs as UID 1000 (what Hugging Face Spaces expects; harmless elsewhere).
RUN apt-get update && apt-get install -y --no-install-recommends redis-server tini \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so code changes don't reinstall them (versions pinned in pyproject.toml).
COPY pyproject.toml ./
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); \
open('/tmp/requirements.txt', 'w').write('\n'.join(d['project']['dependencies']))" \
    && pip install -r /tmp/requirements.txt

# The deployed retriever's models are baked in, so the first request does not download 200 MB.
COPY agent/config.yaml agent/config.yaml
COPY rag/ rag/
RUN RAG_SCORE_CACHE=/tmp/warm.sqlite3 python -c "from rag.models import _embedder, _reranker, embedding_name, \
reranker_name; list(_embedder(embedding_name()).embed(['warm'])); list(_reranker(reranker_name()).rerank('q', ['p']))" \
    && rm -f /tmp/warm.sqlite3*

COPY . .
COPY --from=web /web/dist apps/web/dist

RUN useradd --create-home --uid 1000 gigbid \
    && mkdir -p data/processed gigsim/state mcp_servers/marketplace/audit mcp_servers/workspace/state \
    && chown -R gigbid:gigbid data/processed gigsim/state mcp_servers/marketplace/audit mcp_servers/workspace/state /opt/models \
    && chmod +x infra/docker/start-all.sh
USER gigbid

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
