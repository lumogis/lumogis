# ─── Developer targets ───────────────────────────────────────────────────────
# These targets are for Lumogis contributors. Users do not need make.
# User install: git clone → cp .env.example .env → docker compose up -d
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: dev build test test-integration test-integration-full lint ingest health logs \
        compose-lint compose-test compose-test-stack-control compose-test-integration \
        demo-seed demo-test demo-ready

# ─── User-facing convenience ─────────────────────────────────────────────────

build:
	docker compose up --build --pull always -d

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

logs:
	docker compose logs orchestrator -f --tail 50

ingest:
	curl -s -X POST http://localhost:8000/ingest \
	  -H "Content-Type: application/json" \
	  -d '{"path": "/data"}' | python3 -m json.tool

# ─── Docker-based CI (no running stack or local Python required) ──────────────

# Run ruff inside the orchestrator container (no local Python needed).
# Dev deps (ruff) are installed on the fly; they are not in the production image.
compose-lint:
	docker compose run --rm orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && ruff check /app && ruff format --check /app"

# Run unit tests inside the orchestrator container (does not require a running stack).
# Dev deps (pytest, pytest-asyncio) are installed on the fly.
compose-test:
	docker compose run --rm orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && python -m pytest tests -x -q"

# Stack-control unit tests (mounts stack-control/; dev deps from stack-control/requirements-dev.txt).
compose-test-stack-control:
	docker compose run --rm -v $(PWD)/stack-control:/sc:rw orchestrator sh -c \
	  "pip install -q -r /sc/requirements-dev.txt && cd /sc && python -m pytest test_main.py -q"

# Run integration tests (requires stack to be up; mounts repo-root tests/ into container).
compose-test-integration:
	docker compose run --rm \
	  -v $(PWD)/tests:/integration-tests:ro \
	  orchestrator \
	  sh -c "pip install -q -r requirements-dev.txt && python -m pytest /integration-tests/integration -v --tb=short -m 'integration and not slow'"

# ─── Developer tools (requires local venv) ───────────────────────────────────

# Requires local venv (contributors only)
lint:
	ruff check orchestrator/
	ruff format --check orchestrator/

# Requires local venv — see CONTRIBUTING.md (orchestrator + stack-control requirements-dev.txt).
test:
	cd orchestrator && python -m pytest -x -q
	cd stack-control && python -m pytest test_main.py -q

# Requires local venv (contributors only). Uses orchestrator venv/deps.
test-integration:
	cd orchestrator && python -m pytest ../tests/integration -v --tb=short -m "integration and not slow"

# Includes slow cases (e.g. wait for signal poll).
test-integration-full:
	cd orchestrator && python -m pytest ../tests/integration -v --tb=short -m integration

dev:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.dev.yml up --build --pull always

# ─── Demo helpers ─────────────────────────────────────────────────────────────

demo-seed: ## Seed demo session data for GIF recording
	bash scripts/demo-session-seed.sh

demo-test: ## Test all demo queries pass before recording
	bash scripts/demo-test.sh

demo-ready: demo-seed demo-test ## Full demo prep in one command
