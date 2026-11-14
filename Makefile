.PHONY: install api worker up down logs ps image gate deploy-dry web-install web web-build web-test api-test test lint eval data-download seed data-validate gigsim gigsim-test mcp-portfolio mcp-marketplace mcp-workspace mcp-test snapshots rag-eval verify-eval rag-suite graphs demo agent-test
install:
	python -m pip install -e ".[dev]"

api:
	uvicorn apps.api.main:app --reload --port 8000

worker:       # Arq worker (needs Redis at REDIS_URL); start the API with GIGBID_RUNNER=worker to use it
	arq worker.main.WorkerSettings

# ---- Docker (step 6): postgres, redis, gigsim, api (+ web), worker; `--profile mcp` adds the MCP servers
up:
	docker compose -f infra/docker-compose.yml up --build -d
	@echo "GigBid: http://localhost:8000   GigSim: http://localhost:8081"

down:
	docker compose -f infra/docker-compose.yml down

logs:
	docker compose -f infra/docker-compose.yml logs -f --tail=100 api worker

ps:
	docker compose -f infra/docker-compose.yml ps

image:
	docker build -f infra/docker/Dockerfile -t gigbid:local .

gate:         # the CI evaluation gate (offline): retrieval, injection/scam rules, claim verification
	python -m evals.runners.ci_gate

deploy-dry:   # build the Hugging Face Space folder without pushing (see docs/RUNBOOK.md, "Deploy")
	infra/deploy/hf_space.sh $${SPACE:-you/gigbid} --dry-run

# ---- Web app (apps/web): React + TypeScript + Vite
web-install:
	cd apps/web && npm ci

web:          # dev server on :5173, proxies /api to :8000
	cd apps/web && npx vite --port 5173

web-build:    # FastAPI serves apps/web/dist when it exists
	cd apps/web && npm run build

web-test:     # Playwright, API mocked; needs `npx playwright install chromium` once
	cd apps/web && npx playwright test

api-test:
	python -m pytest tests/integration/test_api.py -q

test:
	pytest -q

lint:
	ruff check .

eval:
	python -m evals.runners.run_demo_eval

# ---- GigSim dataset (data/pipeline) -------------------------------------
data-download:
	python data/pipeline/download.py

seed:
	python data/pipeline/run.py

data-validate:
	python data/pipeline/validate.py

# ---- GigSim marketplace simulator ---------------------------------------
gigsim:
	uvicorn gigsim.app:app --port 8081

gigsim-test:
	GIGSIM_CHAOS=0 python -m pytest gigsim/tests -q

# ---- MCP servers (Streamable HTTP with bearer auth; stdio: python -m mcp_servers.<name>.server)
mcp-portfolio:
	python -m mcp_servers.portfolio.server --http --port 8201

mcp-marketplace:
	python -m mcp_servers.marketplace.server --http --port 8202

mcp-workspace:
	python -m mcp_servers.workspace.server --http --port 8203

mcp-test:
	GIGSIM_CHAOS=0 python -m pytest mcp_servers tests/contract -q

snapshots:
	UPDATE_SNAPSHOTS=1 python -m pytest tests/contract -q

# ---- Retrieval (rag/) and agents (agent/) -------------------------------
rag-eval:
	python -m evals.runners.rag_eval --tag variants

verify-eval:
	python -m evals.runners.verify_eval

rag-suite:   # everything in evals/results/rag_*; sequential (one model-loading process at a time)
	bash evals/runners/run_rag_suite.sh

graphs:
	python -m agent.graphs.render

demo:        # needs `make gigsim` running; stops at the approval gate
	python -m evals.runners.demo_bid --persona ai_ml

agent-test:
	python -m pytest tests/unit -q
