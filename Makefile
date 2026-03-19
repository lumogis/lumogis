.PHONY: setup dev build test test-integration test-integration-full lint ingest health logs

setup:
	@bash scripts/setup.sh

dev:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.dev.yml up

build:
	docker compose up --build -d

test:
	cd orchestrator && python -m pytest -x -q

# Requires: docker compose up -d. Uses orchestrator venv/deps (pytest + httpx in requirements.txt).
test-integration:
	cd orchestrator && python -m pytest ../tests/integration -v --tb=short -m "integration and not slow"

# Includes slow cases (e.g. wait for signal poll).
test-integration-full:
	cd orchestrator && python -m pytest ../tests/integration -v --tb=short -m integration

lint:
	ruff check orchestrator/
	ruff format --check orchestrator/

ingest:
	curl -s -X POST http://localhost:8000/ingest \
	  -H "Content-Type: application/json" \
	  -d '{"path": "/data"}' | python3 -m json.tool

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

logs:
	docker compose logs orchestrator -f --tail 50
