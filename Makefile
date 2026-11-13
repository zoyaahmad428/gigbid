.PHONY: install api test lint eval data-download seed data-validate gigsim gigsim-test mcp-portfolio mcp-marketplace mcp-workspace mcp-test snapshots
install:
	python -m pip install -e ".[dev]"

api:
	uvicorn apps.api.main:app --reload --port 8000

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
	uvicorn gigsim.app:app --port 8100

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
