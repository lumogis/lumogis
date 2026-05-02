# ─── Developer targets ───────────────────────────────────────────────────────
# These targets are for Lumogis contributors. Users do not need make.
# User install: git clone → cp .env.example .env → docker compose up -d
# ─────────────────────────────────────────────────────────────────────────────
#
# Local unit/integration targets use $(PYTHON). Default is `python3` (works on
# hosts with no `python` shim). After `source .venv/bin/activate`, either form
# works; override: `make test PYTHON=python`.
PYTHON ?= python3

.PHONY: dev build test test-unit test-integration test-integration-full lint ingest health logs \
        compose-lint compose-test compose-test-stack-control compose-test-integration \
        mock-capability-test \
        sync-vendored test-kg test-kg-image compose-test-kg \
        test-graph-parity \
        demo-seed demo-test demo-ready \
        web-install web-codegen web-codegen-check web-test web-lint web-build web-dev web-e2e \
        test-web test-web-e2e test-ui test-ui-existing-stack test-ui-full test-ui-full-existing-stack \
        test-migrations \
        verify-public-rc verify-public-rc-full test-integration-local \
        web-e2e-prove web-caddy-headers web-caddy-headers-prove

# Compose overlay + env file for deterministic integration smoke (see config/test.env.example).
INTEGRATION_ENV_FILE ?= config/test.env.example
INTEGRATION_COMPOSE = COMPOSE_PROFILES= COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml docker compose --env-file $(INTEGRATION_ENV_FILE)

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
# Dev deps (pytest, pytest-asyncio) are installed on the fly; **runtime**
# `requirements.txt` is installed first so new pins (e.g. `pywebpush`) apply
# without rebuilding the Docker image when using the mounted `/project` tree.
#
# We `cd /project/orchestrator` so pytest discovers the LIVE source mounted via
# docker-compose.yml's `.:/project` rather than the COPY'd /app snapshot. That
# way local edits to tests or sources show up without rebuilding the image,
# and tests that resolve repo paths via `Path(__file__).resolve().parents[2]`
# (e.g. test_secret_sentinels.py, test_default_registration_disabled.py)
# can find docker-compose.yml + .env.example at the repo root.
# Force AUTH_ENABLED=false so host .env (e.g. local smoke / family-LAN) does not
# leak into TestClient runs — most suites assume dev-mode auth unless they monkeypatch.
compose-test:
	docker compose run --rm -e AUTH_ENABLED=false -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements.txt && pip install -q -r requirements-dev.txt && python -m pytest tests -x -q"

# Stack-control unit tests (mounts stack-control/; dev deps from stack-control/requirements-dev.txt).
compose-test-stack-control:
	docker compose run --rm -v $(PWD)/stack-control:/sc:rw orchestrator sh -c \
	  "pip install -q -r /sc/requirements-dev.txt && cd /sc && python -m pytest test_main.py -q"

# Run integration tests (requires stack to be up; mounts repo-root tests/ into container).
# Uses the FalkorDB overlay so graph integration tests can run against a live instance.
# If FalkorDB is not in COMPOSE_FILE, graph tests are skipped automatically.
compose-test-integration:
	COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml \
	docker compose run --rm \
	  -v $(PWD)/tests:/integration-tests:ro \
	  orchestrator \
	  sh -c "pip install -q -r requirements-dev.txt && python -m pytest /integration-tests/integration -v --tb=short -m 'integration and not slow and not manual'"

# Phase 5 dev-only second capability (not part of default compose); see services/lumogis-mock-capability/README.md
mock-capability-test:
	cd services/lumogis-mock-capability && $(PYTHON) -m pip install -q -r requirements-dev.txt && $(PYTHON) -m pytest tests -q

# ─── Developer tools (requires local venv) ───────────────────────────────────

# Requires local venv (contributors only)
lint:
	ruff check orchestrator/
	ruff format --check orchestrator/

# Requires local venv — see CONTRIBUTING.md (orchestrator + stack-control requirements-dev.txt).
# Orchestrator route tests use unauthenticated TestClient with the synthetic `default`
# user when `require_user` no-ops — that requires AUTH_ENABLED=false unless every test
# supplies a bearer token. Host shells often export AUTH_ENABLED=true from compose.
test:
	cd orchestrator && AUTH_ENABLED=false $(PYTHON) -m pytest -x -q
	cd stack-control && $(PYTHON) -m pytest test_main.py -q

# Public RC / CI unit gate — uses a repo-local `.venv` (created if missing) so PEP 668
# hosts can install deps without Docker; CI runners also tolerate this path.
test-unit:
	@set -e; \
	  if [ ! -d "$(CURDIR)/.venv" ]; then $(PYTHON) -m venv "$(CURDIR)/.venv"; fi; \
	  . "$(CURDIR)/.venv/bin/activate"; \
	  pip install -q -r orchestrator/requirements.txt -r orchestrator/requirements-dev.txt; \
	  pip install -q -r stack-control/requirements-dev.txt; \
	  pip install -q -r services/lumogis-mock-capability/requirements-dev.txt; \
	  pip install -q -r services/lumogis-graph/requirements.txt -r services/lumogis-graph/requirements-dev.txt; \
	  cd "$(CURDIR)/orchestrator" && AUTH_ENABLED=false GRAPH_MODE=inprocess CAPABILITY_SERVICE_URLS= MCP_AUTH_TOKEN= pytest tests/ -q; \
	  cd "$(CURDIR)/stack-control" && pytest test_main.py -q; \
	  cd "$(CURDIR)/services/lumogis-mock-capability" && PYTHONPATH=. pytest tests -q; \
	  cd "$(CURDIR)/services/lumogis-graph" && pytest -q

# Lumogis Web — codegen from committed OpenAPI snapshot, then lint + Vitest + production build.
test-web:
	cd clients/lumogis-web && npm ci && npm run codegen && npm run lint && npm test && npm run build

# Compose-backed integration smoke — see scripts/integration-public-rc.sh.
test-integration:
	bash scripts/integration-public-rc.sh full-cycle

# Playwright gate UI — requires RC compose + Caddy already up (see test-ui).
test-ui-existing-stack:
	cd clients/lumogis-web && npm ci && npx playwright install chromium && npm run e2e:gate-ui

# Standalone Playwright gate UI: bring RC stack up → gate suite → tear down.
test-ui:
	bash -ec 'scripts/integration-public-rc.sh gate-start; ec=0; $(MAKE) test-ui-existing-stack || ec=$$?; scripts/integration-public-rc.sh gate-end; exit $$ec'

# Full Playwright (gate + workflows + signed-in nav) — stack must already be running + smoke user seeded when auth is on.
test-ui-full-existing-stack:
	eval "$$(python3 "$(CURDIR)/scripts/rc_test_env_defaults.py" "$(CURDIR)/$(INTEGRATION_ENV_FILE)")"; \
	cd clients/lumogis-web && npm ci && npx playwright install chromium && npm run e2e:full

# Second-phase UI gate used by verify-public-rc-full: compose up → seed smoke user → e2e:full → compose down.
test-ui-full:
	bash -ec 'scripts/integration-public-rc.sh gate-start; scripts/seed-public-rc-smoke-user.sh; ec=0; $(MAKE) test-ui-full-existing-stack || ec=$$?; scripts/integration-public-rc.sh gate-end; exit $$ec'

test-migrations:
	bash scripts/check-migrations-fresh-db.sh

# Tier used before merging RC to main: hygiene + unit/web/integration/ui + export tree checks.
# Brings the compose test stack up once, runs integration pytest + Playwright (existing stack), then tears down.
verify-public-rc:
	bash scripts/check-main-hygiene.sh
	bash scripts/check-protected-release-files.sh
	$(MAKE) test-unit
	$(MAKE) test-web
	bash -ec 'scripts/integration-public-rc.sh gate-start; scripts/integration-public-rc.sh gate-pytest; ec=$$?; if [ $$ec -ne 0 ]; then scripts/integration-public-rc.sh gate-end; exit $$ec; fi; $(MAKE) test-ui-existing-stack || ec=$$?; scripts/integration-public-rc.sh gate-end; exit $$ec'
	bash scripts/create-upstream-export-tree.sh /tmp/lumogis-upstream-export
	bash scripts/check-public-export.sh /tmp/lumogis-upstream-export

# verify-public-rc plus seeded smoke user + full UI (signed-in nav), then heavier Docker checks.
verify-public-rc-full:
	$(MAKE) verify-public-rc
	$(MAKE) test-migrations
	$(MAKE) test-ui-full
	@echo "[verify-public-rc-full] optional: compose-test"
	-$(MAKE) compose-test
	@echo "[verify-public-rc-full] optional: compose-test-kg"
	-$(MAKE) compose-test-kg
	@echo "[verify-public-rc-full] optional: test-graph-parity (slow)"
	-$(MAKE) test-graph-parity

# Requires local venv (contributors only). Uses orchestrator venv/deps — broader than public_rc.
test-integration-local:
	cd orchestrator && $(PYTHON) -m pytest ../tests/integration -v --tb=short -m "integration and not slow"

# Includes slow cases (e.g. wait for signal poll).
test-integration-full:
	cd orchestrator && $(PYTHON) -m pytest ../tests/integration -v --tb=short -m integration

dev:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.dev.yml up --build --pull always

# ─── lumogis-graph (out-of-process KG service) ────────────────────────────────

# Re-vendor Core's canonical models/webhook.py into the KG service tree.
# The KG service must NEVER drift from Core's wire contract; this target is the
# single supported way to update the vendored copy. Run after editing the
# canonical orchestrator/models/webhook.py and commit both files together.
# Adds the standard "VENDORED FROM ... DO NOT EDIT BY HAND" header so the
# provenance is obvious to anyone opening the KG copy.
sync-vendored:
	@for name in webhook capability; do \
	  src=orchestrator/models/$$name.py; \
	  dst=services/lumogis-graph/models/$$name.py; \
	  test -f $$src || { echo "ERROR: $$src not found"; exit 1; }; \
	  { \
	    head -n 2 $$src; \
	    echo "# VENDORED FROM orchestrator/models/$$name.py — DO NOT EDIT BY HAND."; \
	    echo '# Run `make sync-vendored` after changing the canonical Core copy.'; \
	    tail -n +3 $$src; \
	  } > $$dst.tmp && mv $$dst.tmp $$dst; \
	  echo "sync-vendored: re-vendored $$src → $$dst"; \
	done

# Run KG service unit tests inside a dedicated lumogis-graph:test image
# (the `test` stage of services/lumogis-graph/Dockerfile). The test stage
# bakes pytest + pytest-asyncio + ruff into the venv at build time so the
# test invocation does NOT do a fresh `pip install` per run (the on-the-fly
# `pip install -r requirements-dev.txt` pattern used by `compose-test` for
# Core hangs on this small service because requirements-dev.txt re-resolves
# the full runtime requirements; baking the deps in skips that pass).
#
# Default env keeps tests from accidentally hitting a real Postgres/FalkorDB:
#   GRAPH_BACKEND=falkordb        — required by main.py:_hard_fail_if_no_falkordb
#   KG_ALLOW_INSECURE_WEBHOOKS=true — webhook tests turn this off explicitly
#   KG_SCHEDULER_ENABLED=false    — keeps register_scheduled_jobs a no-op
test-kg-image:
	docker build --target test -f services/lumogis-graph/Dockerfile \
	  -t lumogis-graph:test .

compose-test-kg: test-kg-image
	docker run --rm \
	  -e GRAPH_BACKEND=falkordb \
	  -e KG_ALLOW_INSECURE_WEBHOOKS=true \
	  -e KG_SCHEDULER_ENABLED=false \
	  -e LOG_LEVEL=ERROR \
	  lumogis-graph:test python -m pytest tests -x -q

# Local-venv variant for contributors with a KG-side venv set up.
test-kg:
	cd services/lumogis-graph && $(PYTHON) -m pytest -x -q

# GRAPH_MODE parity test: ingests the same fixture corpus under
# `GRAPH_MODE=inprocess` and `GRAPH_MODE=service` and asserts the
# resulting FalkorDB snapshots are identical. Slow (boots/tears down
# the full stack twice) — not part of the default `test-integration`
# target. Requires Docker. The test itself self-skips if `docker` is
# not on PATH so contributors without Docker can still collect it.
test-graph-parity:
	cd orchestrator && $(PYTHON) -m pytest \
	  ../tests/integration/test_graph_parity.py -v --tb=short \
	  -m 'integration and slow'

# ─── Lumogis Web (clients/lumogis-web/) ──────────────────────────────────────
# Phase 1 Pass 1.1 introduced the React + TypeScript SPA. These targets run
# everything locally (npm + node ≥ 20). CI mirrors them in clients/lumogis-web/.

web-install:
	cd clients/lumogis-web && npm install

web-codegen:
	cd clients/lumogis-web && npm run codegen

# CI gate per parent plan §"Phase 1 Pass 1.1 item 1" — fail if the committed
# OpenAPI snapshot drifts from the live orchestrator spec. Requires the
# orchestrator to be reachable at $LUMOGIS_OPENAPI_URL (default
# http://localhost:8000/openapi.json).
web-codegen-check:
	cd clients/lumogis-web && npm run codegen:check

web-test:
	cd clients/lumogis-web && npm test

web-lint:
	cd clients/lumogis-web && npm run lint

web-build:
	cd clients/lumogis-web && npm run build

web-dev:
	cd clients/lumogis-web && npm run dev

# Playwright e2e (Phase 1 Pass 1.5; FP-046 me/admin shell spec included). Requires stack
# + Caddy on PLAYWRIGHT_BASE_URL (default http://127.0.0.1) and
# LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_WEB_SMOKE_PASSWORD.
# One-time browser install: cd clients/lumogis-web && npx playwright install chromium
web-e2e:
	cd clients/lumogis-web && npm run e2e

# Alias: same as `web-e2e` (backlog / docs FP-046)
test-web-e2e: web-e2e

# Hard fail if smoke creds are missing (CI-style proof). Requires running stack +
# Caddy on PLAYWRIGHT_BASE_URL (default http://127.0.0.1) and valid
# LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_WEB_SMOKE_PASSWORD in the environment.
web-e2e-prove:
	cd clients/lumogis-web && npm run e2e:prove

# Requires stack up (docker compose up -d). Uses the orchestrator image (pytest+httpx)
# and fetches the Caddy service at http://caddy (set LUMOGIS_WEB_BASE_URL to override).
# Skips if Caddy is unreachable unless LUMOGIS_CADDY_HEADER_PROVE=1 (then fails).
web-caddy-headers:
	docker compose run --rm -e LUMOGIS_WEB_BASE_URL=$${LUMOGIS_WEB_BASE_URL:-http://caddy} \
	  -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && python -m pytest ../tests/integration/test_caddy_security_headers.py -m integration -q"

# Same as web-caddy-headers but fails when the front door is down (for automation).
web-caddy-headers-prove:
	docker compose run --rm -e LUMOGIS_CADDY_HEADER_PROVE=1 \
	  -e LUMOGIS_WEB_BASE_URL=$${LUMOGIS_WEB_BASE_URL:-http://caddy} \
	  -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && python -m pytest ../tests/integration/test_caddy_security_headers.py -m integration -q"

# ─── Demo helpers ─────────────────────────────────────────────────────────────

demo-seed: ## Seed demo session data for GIF recording
	bash scripts/demo-session-seed.sh

demo-test: ## Test all demo queries pass before recording
	bash scripts/demo-test.sh

demo-ready: demo-seed demo-test ## Full demo prep in one command
