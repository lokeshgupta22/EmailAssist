VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: help setup model run test lint fmt evals clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install dependencies
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

model: ## Download the local language model (needs Ollama installed)
	ollama pull qwen3:4b

run: ## Start the web app on http://127.0.0.1:8000
	$(VENV)/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

test: ## Run the test suite (skips tests needing Ollama)
	$(PY) -m pytest -m "not integration"

lint: ## Check formatting and lint rules
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

fmt: ## Auto-format the code
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

evals: ## Score the pipeline against the golden dataset (needs Ollama)
	$(PY) -m evals.run_evals

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
