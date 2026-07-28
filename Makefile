# KnowledgeOS AI — one command per thing you actually need to do.
#
# `make up` should take a clean clone to a working application. Anything that
# needs a paragraph of instructions belongs in here instead.

SHELL := /bin/bash

# Compose ships two ways: as a `docker compose` plugin, and as a standalone
# `docker-compose` binary. Hardcoding either breaks on machines that have the
# other, so detect it once.
COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
BACKEND := cd backend &&
VENV := backend/.venv/bin

.DEFAULT_GOAL := help
.PHONY: help up down logs ps migrate revision seed test eval lint format typecheck check \
        dev-api dev-worker dev-web install clean nuke scan demo

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

# ── running ──────────────────────────────────────────────────────────────

up: ## Build and start the whole stack (migrations run first)
	@test -f .env || (cp .env.example .env && \
	  python3 -c "import secrets,pathlib; p=pathlib.Path('.env'); \
	    p.write_text(p.read_text().replace('SECRET_KEY=', 'SECRET_KEY='+secrets.token_urlsafe(48)))" && \
	  echo "Created .env with a generated SECRET_KEY.")
	$(COMPOSE) up -d --build
	@echo
	@echo "  Console   http://localhost:3000"
	@echo "  API docs  http://localhost:8000/docs"
	@echo "  Health    http://localhost:8000/readyz"

down: ## Stop the stack, keep the data
	$(COMPOSE) down

logs: ## Follow logs from every service
	$(COMPOSE) logs -f --tail=80

ps: ## Show service status
	$(COMPOSE) ps

# ── database ─────────────────────────────────────────────────────────────

migrate: ## Apply migrations to head
	$(BACKEND) .venv/bin/alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add widgets"
	$(BACKEND) .venv/bin/alembic revision --autogenerate -m "$(m)"

seed: ## Create a demo account and ingest the design document
	$(VENV)/python scripts/seed.py

# ── quality ──────────────────────────────────────────────────────────────

eval: ## Measure retrieval quality (ABLATE=1 to compare dense/sparse/hybrid)
	$(BACKEND) ../$(VENV)/python evals/harness.py $(if $(ABLATE),--ablate,)

test: ## Run the test suite against real Postgres, Redis and Qdrant
	$(BACKEND) ENVIRONMENT=test LLM_PROVIDER=scripted ../$(VENV)/pytest tests -q

lint: ## Lint backend and frontend
	$(BACKEND) ../$(VENV)/ruff check app tests
	cd frontend && npx tsc --noEmit

format: ## Format the backend
	$(BACKEND) ../$(VENV)/ruff format app tests
	$(BACKEND) ../$(VENV)/ruff check app tests --fix

typecheck: ## Type-check the backend
	$(BACKEND) ../$(VENV)/mypy app

check: lint typecheck test ## Everything CI runs

scan: ## Fail if anything resembling a secret is committed
	@echo "Scanning tracked files and full history for credentials…"
	@! git grep -nIE '(sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' -- \
	  ':!*.lock' ':!Makefile' || (echo "SECRET FOUND in working tree" && exit 1)
	@! git log -p --all | grep -nIE '(sk-ant-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' \
	  || (echo "SECRET FOUND in history" && exit 1)
	@git ls-files | grep -qxF '.env' && (echo ".env is tracked" && exit 1) || true
	@echo "Clean."

# ── local development (no containers) ────────────────────────────────────

install: ## Install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npm install

dev-api: ## Run the API with reload
	$(BACKEND) LOG_JSON=false ../$(VENV)/uvicorn app.main:app --reload --port 8730

dev-worker: ## Run the ingestion worker
	$(BACKEND) LOG_JSON=false ../$(VENV)/python -m app.worker

dev-web: ## Run the Next.js dev server
	cd frontend && npm run dev

# ── housekeeping ─────────────────────────────────────────────────────────

clean: ## Remove build artefacts and caches
	find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache frontend/.next

nuke: ## Stop everything and DELETE all data volumes
	$(COMPOSE) down -v
