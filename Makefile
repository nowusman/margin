.PHONY: bootstrap up down test lint migrate evaluate surfaces sbom scan mcp

mcp:
	python -m margin_mcp.main --api-url $(MARGIN_MCP_API_URL)

EVALUATION_PYTHONPATH = .:apps/api:packages/margin_auth:packages/margin_db:packages/margin_schema:packages/margin_embedding:packages/margin_retrieval:packages/margin_eval:packages/margin_ingest:packages/margin_inference:packages/margin_graph:packages/margin_agent

bootstrap:
	python -m pip install --upgrade pip
	python -m pip install -r apps/api/requirements-dev.txt
	npm install --prefix apps/web

up:
	docker compose -f infra/docker-compose.yml up --build

down:
	docker compose -f infra/docker-compose.yml down

test:
	python -m pytest
	npm --prefix apps/web run test

lint:
	python -m black --check apps/api packages workers tests
	python -m isort --check-only apps/api packages workers tests
	python -m ruff check .
	python -m flake8 apps packages workers tests
	python -m mypy apps packages workers
	python scripts/check_runtime_guards.py
	npm --prefix apps/web run lint
	npm --prefix apps/web run format:check
	npm --prefix apps/web run markdown:check

migrate:
	python -m alembic -c packages/margin_db/alembic.ini upgrade head

evaluate:
	PYTHONPATH=$(EVALUATION_PYTHONPATH) python -m margin_eval.cli corpus

surfaces:
	PYTHONPATH=$(EVALUATION_PYTHONPATH) python -m margin_eval.cli surfaces --output .margin/surface_evaluation_report.json

sbom:
	syft dir:. -o cyclonedx-json > sbom.cdx.json

scan:
	grype dir:. --fail-on critical
