.PHONY: setup dev build test test-integration test-integration-full lint ingest health logs

# Compose working directory: use path in .compose-root if present (e.g. /home/thomas/lumogis),
# so the same stack is used when coding in Cursor from a different repo path.
COMPOSE_ROOT := $(shell cat .compose-root 2>/dev/null || echo ".")
COMPOSE_BASE := -f $(COMPOSE_ROOT)/docker-compose.yml -f $(COMPOSE_ROOT)/docker-compose.gpu.yml
COMPOSE := docker compose --project-directory $(COMPOSE_ROOT) $(COMPOSE_BASE)

setup:
	@bash scripts/setup.sh $(if $(ROOT),--root "$(ROOT)",)

dev:
	$(COMPOSE) -f $(COMPOSE_ROOT)/docker-compose.dev.yml up

build:
	$(COMPOSE) up --build -d

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
	$(COMPOSE) logs orchestrator -f --tail 50
