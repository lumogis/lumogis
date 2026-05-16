# ─── Developer targets ───────────────────────────────────────────────────────
# These targets are for Lumogis contributors. Users do not need make.
# User install: git clone → cp .env.example .env → docker compose up -d
# ─────────────────────────────────────────────────────────────────────────────
#
# Local unit/integration targets use $(PYTHON). Default is `python3` (works on
# hosts with no `python` shim). After `source .venv/bin/activate`, either form
# works; override: `make test PYTHON=python`.
PYTHON ?= python3

.PHONY: dev build test test-integration test-integration-full lint ingest health logs \
        audit-local web-audit-fix \
        compose-policy-check \
        graph-relates-to-merge-policy-check \
        verify-public-rc verify-public-rc-full \
        compose-lint compose-test compose-test-stack-control compose-test-integration \
        compose-policy-check compose-policy-check-baseline compose-policy-check-adversarial \
        compose-policy-check-adversarial-envfile \
        mock-capability-test \
        sync-vendored test-kg test-kg-image compose-test-kg \
        test-graph-parity \
        demo-seed demo-test demo-ready \
        web-install web-codegen web-codegen-check web-dockerfile-check web-docker-build web-test web-lint web-build web-dev web-e2e \
        test-web-e2e \
        web-e2e-prove web-caddy-headers web-caddy-headers-prove \
        m1-compat-with-retry \
        auth-sessions-grep-guard \
        changelog-check \
        verify-no-telemetry

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

# LUM-43 — compose policy (host Python + PyYAML + Docker Compose CLI).
compose-policy-check-baseline:
	$(PYTHON) scripts/check_compose_policy.py -f docker-compose.yml

# compose-policy-check: mock overlay needs MOCK_CAPABILITY_SHARED_SECRET for `docker compose config`.
# Also validates docker-compose.ghcr.yml (CI publish path).

# Expect checker exit 1 (policy violation); make exit 0 means the guard caught the regression.
compose-policy-check-adversarial:
	@$(PYTHON) scripts/check_compose_policy.py -f docker-compose.yml -f docker-compose.test-policy-adversarial.yml; \
	ec=$$?; \
	if [ $$ec -eq 1 ]; then exit 0; fi; \
	echo "compose-policy-check-adversarial: expected policy violation (exit 1), got $$ec" >&2; \
	exit 1

compose-policy-check-adversarial-envfile:
	@$(PYTHON) scripts/check_compose_policy.py -f docker-compose.yml -f docker-compose.test-policy-adversarial-envfile.yml; \
	ec=$$?; \
	if [ $$ec -eq 1 ]; then exit 0; fi; \
	echo "compose-policy-check-adversarial-envfile: expected policy violation (exit 1), got $$ec" >&2; \
	exit 1

# ─── Docker-based CI (no running stack or local Python required) ──────────────

# LUM-29 — forbid legacy ``refresh_token_jti`` mentions in orchestrator prod modules.
auth-sessions-grep-guard:
	@$(PYTHON) scripts/check_refresh_token_jti_guard.py

# LUM-193 — optional pre-push mirror of the CI changelog path gate.
changelog-check:
	@scripts/check-changelog-touched.sh

verify-no-telemetry:
	grep -r "posthog\|mixpanel\|amplitude" orchestrator/ && echo "FAIL: analytics library found" && exit 1 || echo "OK: no analytics libraries found"

# Run ruff inside the orchestrator container (no local Python needed).
# Dev deps (ruff) are installed on the fly; they are not in the production image.
compose-lint:
	COMPOSE_FILE=docker-compose.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm --entrypoint "" orchestrator \
	  sh -c 'pip install -q -r /project/orchestrator/requirements-dev.txt && \
	         ruff check /project/orchestrator/ && \
	         ruff format --check /project/orchestrator/ && \
	         python /project/scripts/check_refresh_token_jti_guard.py'

compose-policy-check:
	@python3 -c "import yaml" 2>/dev/null || \
	  python3 -m pip install -q -r scripts/requirements-compose-policy.txt
	MOCK_CAPABILITY_SHARED_SECRET=lumogis-ci-mock-capability-placeholder \
	  $(PYTHON) scripts/check_compose_policy.py -f docker-compose.yml -f docker-compose.mock-capability.yml
	@python3 scripts/check_compose_policy.py \
	  --project-directory "$(CURDIR)" \
	  -f docker-compose.yml \
	  -f docker-compose.ghcr.yml

# ─── RC verification gates (LUM-225) ─────────────────────────────────────────
# verify-public-rc: smoke gate — run before /publish-private-main-to-public.
# verify-public-rc-full: full gate — includes Playwright e2e + optional graph parity.
#
# web-codegen-check runs scripts.dump_openapi (no running orchestrator).
# Set VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK=1 to skip (non-default; requires
# Implementation Log justification).
# Set VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1 on verify-public-rc (only) to skip
# integration-public-rc.sh (dev machines with production stacks); never for publish prep.
# Set VERIFY_PUBLIC_RC_SKIP_WEB_E2E=1 in full mode to skip Playwright (discouraged).
# Set LUMOGIS_RC_GRAPH_PARITY=1 in full mode to include test-graph-parity.

graph-relates-to-merge-policy-check: ## LUM-208 — AST-aware scan for invalid RELATES_TO MERGE shapes
	$(PYTHON) scripts/check_graph_relates_to_merge_policy.py

m1-compat-with-retry: ## Live FalkorDB compat gate (requires FALKORDB_URL + RUN_M1_COMPAT=1); one retry on flake
	cd orchestrator && (RUN_M1_COMPAT=1 $(PYTHON) -m pytest tests/premium/test_graph_writer.py::TestFalkorDBCompatGate -q || (sleep 2 && RUN_M1_COMPAT=1 $(PYTHON) -m pytest tests/premium/test_graph_writer.py::TestFalkorDBCompatGate -q))

# NOTE: Must run on an export-shaped RC branch (after
# create-upstream-export-tree.sh has stripped docs/private/ and other
# private paths). Will fail by design on raw dev/main private checkouts
# where docs/private/ is tracked.
verify-public-rc: ## RC gate (smoke) — run before /publish-private-main-to-public
	@set -e; \
	echo "==> verify-public-rc (smoke)"; \
	_qdrant_user_set=0; \
	[ -n "$${QDRANT_HOST_PORT:-}" ] && _qdrant_user_set=1; \
	export QDRANT_HOST_PORT="$${QDRANT_HOST_PORT:-$$($(CURDIR)/scripts/integration-public-rc.sh print-qdrant-host-port)}"; \
	echo "[verify-public-rc] Using QDRANT_HOST_PORT=$$QDRANT_HOST_PORT"; \
	scripts/check-main-hygiene.sh; \
	$(MAKE) compose-policy-check; \
	$(MAKE) graph-relates-to-merge-policy-check; \
	$(MAKE) compose-test; \
	if [ -z "$${VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK:-}" ]; then \
	  $(MAKE) web-codegen-check; \
	else \
	  echo "WARN: web-codegen-check skipped (VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK set)"; \
	fi; \
	$(MAKE) web-lint; \
	$(MAKE) web-test; \
	$(MAKE) web-build; \
	if [ "$${VERIFY_PUBLIC_RC_SKIP_INTEGRATION:-}" != "1" ] && [ "$$_qdrant_user_set" -eq 0 ]; then \
	  export QDRANT_HOST_PORT="$$(env -u QDRANT_HOST_PORT $(CURDIR)/scripts/integration-public-rc.sh print-qdrant-host-port)"; \
	  echo "[verify-public-rc] Using QDRANT_HOST_PORT=$$QDRANT_HOST_PORT (integration stack)"; \
	fi; \
	if [ "$${VERIFY_PUBLIC_RC_FORCE_INTEGRATION:-}" = "1" ]; then \
	  scripts/integration-public-rc.sh full-cycle; \
	elif [ "$${VERIFY_PUBLIC_RC_SKIP_INTEGRATION:-}" = "1" ]; then \
	  echo "WARN: integration step skipped (VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1) — only use this on dev machines with live production stacks"; \
	else \
	  scripts/integration-public-rc.sh full-cycle; \
	fi; \
	scripts/create-upstream-export-tree.sh; \
	scripts/check-public-export.sh /tmp/lumogis-upstream-export; \
	echo "==> verify-public-rc PASSED"

# Runs verify-public-rc with VERIFY_PUBLIC_RC_FORCE_INTEGRATION=1 so integration
# always executes even if VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1 is set in the environment.
verify-public-rc-full: ## Full RC gate — includes e2e and optional graph parity
	@echo "==> verify-public-rc-full"
	@VERIFY_PUBLIC_RC_FORCE_INTEGRATION=1 $(MAKE) verify-public-rc
	@if [ -z "$${VERIFY_PUBLIC_RC_SKIP_WEB_E2E:-}" ]; then \
	  $(MAKE) web-e2e-prove; \
	else \
	  echo "WARN: web-e2e-prove skipped (VERIFY_PUBLIC_RC_SKIP_WEB_E2E set)"; \
	fi
	@if [ "$${LUMOGIS_RC_GRAPH_PARITY:-0}" = "1" ]; then \
	  $(MAKE) test-graph-parity; \
	fi
	@echo "==> verify-public-rc-full PASSED"

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
# Orchestrator depends on Qdrant; avoid host port clash when dev stack already publishes Qdrant on 6334.
compose-test:
	COMPOSE_FILE=docker-compose.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm -e AUTH_ENABLED=false -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements.txt && pip install -q -r requirements-dev.txt && python -m pytest tests -x -q"

# Stack-control unit tests (mounts stack-control/; dev deps from stack-control/requirements-dev.txt).
compose-test-stack-control:
	COMPOSE_FILE=docker-compose.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm -v $(PWD)/stack-control:/sc:rw orchestrator sh -c \
	  "pip install -q -r /sc/requirements-dev.txt && cd /sc && python -m pytest test_main.py -q"

# Run integration tests (requires stack to be up; mounts repo-root tests/ into container).
# Uses the FalkorDB overlay so graph integration tests can run against a live instance.
# If FalkorDB is not in COMPOSE_FILE, graph tests are skipped automatically.
compose-test-integration:
	COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm \
	  -v $(PWD)/tests:/integration-tests:ro \
	  orchestrator \
	  sh -c "pip install -q -r requirements-dev.txt && python -m pytest /integration-tests/integration -v --tb=short -m 'integration and not slow and not manual'"

# Phase 5 dev-only second capability (not part of default compose); see services/lumogis-mock-capability/README.md
mock-capability-test:
	cd services/lumogis-mock-capability && $(PYTHON) -m pip install -q -r requirements-dev.txt && $(PYTHON) -m pytest tests -q

# ─── Developer tools (requires local venv) ───────────────────────────────────

# Dependency CVE audit (npm audit + pip-audit). Free/local only; needs network for advisory DBs.
# Bootstrap: creates .venv-audit/ when pip-audit is not on PATH (gitignored).
# Optional env: LUMOGIS_DEVTOOLS=/path/to/lumogis-devtools (default ../lumogis-devtools).
# AUDIT_SKIP_NPM=1 or AUDIT_SKIP_PIP=1 to scan one ecosystem only.
audit-local:
	bash scripts/audit_local.sh

# npm audit fix must run under clients/lumogis-web (package-lock.json lives there; repo root has none).
web-audit-fix:
	cd clients/lumogis-web && npm audit fix

# Requires local venv (contributors only)
lint:
	ruff check orchestrator/
	ruff format --check orchestrator/
	@$(PYTHON) scripts/check_refresh_token_jti_guard.py

# Requires local venv — see CONTRIBUTING.md (orchestrator + stack-control requirements-dev.txt).
# Orchestrator route tests use unauthenticated TestClient with the synthetic `default`
# user when `require_user` no-ops — that requires AUTH_ENABLED=false unless every test
# supplies a bearer token. Host shells often export AUTH_ENABLED=true from compose.
test:
	cd orchestrator && AUTH_ENABLED=false $(PYTHON) -m pytest -x -q
	cd stack-control && $(PYTHON) -m pytest test_main.py -q

# Requires local venv (contributors only). Uses orchestrator venv/deps.
test-integration:
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
# OpenAPI snapshot drifts from the orchestrator spec (via scripts.dump_openapi).
# Ensures a repo-local venv has orchestrator imports (PEP 668–safe); same tree as
# scripts/integration-public-rc.sh uses optional .venv.
web-codegen-check:
	@test -d "$(CURDIR)/.venv" || $(PYTHON) -m venv "$(CURDIR)/.venv"
	"$(CURDIR)/.venv/bin/pip" install -q -r orchestrator/requirements.txt
	cd clients/lumogis-web && npm run codegen:check

# LUM-224 — fail if Dockerfile drops lockfile COPY or npm ci (supply-chain regression guard).
web-dockerfile-check:
	@grep -qF 'COPY package.json package-lock.json' clients/lumogis-web/Dockerfile \
	  || (echo "web-dockerfile-check: clients/lumogis-web/Dockerfile must COPY package.json package-lock.json" >&2; exit 1)
	@grep -qE '^[[:space:]]*RUN[[:space:]]+npm ci([[:space:]]|$$)' clients/lumogis-web/Dockerfile \
	  || (echo "web-dockerfile-check: clients/lumogis-web/Dockerfile must RUN npm ci" >&2; exit 1)

# LUM-254 — same image build CI exercises (requires Docker; run from repo root).
web-docker-build:
	docker compose build lumogis-web

web-test:
	cd clients/lumogis-web && npm test

web-lint:
	cd clients/lumogis-web && npm run lint

web-build:
	cd clients/lumogis-web && npm run codegen && npm run build

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
