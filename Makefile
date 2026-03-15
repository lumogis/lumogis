.PHONY: dev build test lint ingest health logs

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

build:
	docker compose up --build -d

test:
	cd orchestrator && python -m pytest -x -q

lint:
	ruff check orchestrator/
	ruff format --check orchestrator/

ingest:
	curl -s -X POST http://localhost:8000/ingest | python3 -m json.tool

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

logs:
	docker compose logs orchestrator -f --tail 50
