.PHONY: install api test lint eval data-download seed data-validate
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
