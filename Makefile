# ─── Developer targets ───────────────────────────────────────────────────────
# These targets are for Lumogis contributors. Users do not need make.
# User install: git clone → cp .env.example .env → docker compose up -d
# ─────────────────────────────────────────────────────────────────────────────
#
# Local unit/integration targets use $(PYTHON). Default is `python3` (works on
# hosts with no `python` shim). After `source .venv/bin/activate`, either form
# works; override: `make test PYTHON=python`.
PYTHON ?= python3
# Optional args for doctor (LUM-199 / LUM-320), e.g. `make doctor ARGS="--json"` (portable) or
# `make doctor ARGS="--fix --dry-run"` (repair dry-run; JSON v2 with `ARGS="--json --fix"`).
ARGS ?=

# LUM-808 — GPU overlay selection for `make dev`.
# The README lists `docker-compose.gpu.yml` under "Composition: required vs
# optional" as an ADD-ON, but `make dev` used to include it unconditionally, so a
# host without the NVIDIA container runtime failed the whole `up` with
#     Error response from daemon: could not select device driver "nvidia" ...
# — a raw daemon error naming a device driver rather than a missing prerequisite.
# Ollama runs fine on CPU; the GPU is an optimisation here, not a requirement.
#   LUMOGIS_GPU=1  → require the overlay; fail loudly if the runtime is absent
#   LUMOGIS_GPU=0  → never use the overlay
#   unset (default) → auto-detect, and say so out loud when falling back to CPU
LUMOGIS_GPU ?=

# LUM-319 / POSIX: recipes use `set -o pipefail` (e.g. `compose-test-doctor`); Ubuntu `/bin/sh` is dash — use bash.
SHELL := /bin/bash

.PHONY: dev build test bench-recall check-pytest test-integration test-integration-full e2e-ingest-restart e2e-ingest-live lint ingest health logs doctor doctor-fix doctor-fix-dry doctor-fix-apply \
        compose-test-paperless check-integration-hermeticity \
        test-env-unit test-env-build test-env-test test-env-integration \
        search-dev search-build search-build-client \
        lumogis-cursor-install test-lumogis-mcp test-cursor-integration test-cursor-integration-full \
        seed-cursor-integration-fixture prove-cursor-integration-full \
        audit-local bandit-check web-audit-fix zap-rc-baseline-lum318 \
        compose-policy-check caddy-keepalive-check \
        graph-relates-to-merge-policy-check \
        verify-public-rc verify-public-rc-full release-doc-sync-check \
        compose-test-backup backup backup-prune backup-verify restore \
        migrate-dry-run update rollback \
        test-backup-retention test-backup-files rescue-captures rescue-stop-entities \
        compose-policy-check compose-policy-check-baseline compose-policy-check-adversarial \
        compose-policy-check-adversarial-envfile compose-policy-check-community \
        compose-policy-check-egress compose-policy-check-name-squat egress-containment-test \
        egress-imap-test egress-guard-test \
        check-egress-acl-divergence \
        check-no-tethered-lum613 \
        mock-capability-test \
        sync-vendored test-kg test-kg-image compose-test-kg compose-test-kg-integration \
        test-graph-parity \
        demo-seed demo-test demo-ready \
        web-install web-codegen web-codegen-check openapi-check openapi-breaking-check web-dockerfile-check shellcheck-web-docker-build-paths shellcheck-ci-paths web-docker-build web-test web-lint web-build web-dev web-e2e \
        test-web-e2e \
        web-e2e-prove web-e2e-ollama-prove web-demo web-screenshots overlay-e2e overlay-e2e-smoke web-caddy-headers web-caddy-headers-prove caddy-client-ip-prove caddy-config-check \
        verify-lum-811 \
        m1-compat-with-retry \
        auth-sessions-grep-guard \
        changelog-check exec-bit-check \
        coverage-matrix-check \
        verify-no-telemetry \
        render-site-pages check-site-pages \
        debug test-list

# ─── User-facing convenience ─────────────────────────────────────────────────

# LUM-892: the rescues (captures; the stop-entities list, LUM-922) sit between
# the (slow) build and the recreate, so the orchestrator is stopped for seconds,
# not for the whole build. The second
# --build is a cache hit; the last line keeps today's pull semantics.
build:
	docker compose build --pull
	bash scripts/update/rescue-captures.sh
	bash scripts/update/rescue-stop-entities.sh
	docker compose up --build --pull always -d

rescue-captures: ## Copy capture media out of the orchestrator container before it is recreated (LUM-892)
	bash scripts/update/rescue-captures.sh

rescue-stop-entities: ## Copy an edited stop-entities list out of the orchestrator container before it is recreated (LUM-922)
	bash scripts/update/rescue-stop-entities.sh

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

logs:
	docker compose logs orchestrator -f --tail 50

# LUM-199 / LUM-320 — operator health CLI + optional --fix (see scripts/doctor/README.md).
doctor:
	@bash "$(CURDIR)/scripts/doctor/run.sh" $(ARGS)

# LUM-343 — ergonomic sugar over `doctor ARGS=...`. No new behaviour or JSON
# contract: each target just prepends the relevant --fix flags, then appends
# $(ARGS) so extra flags (e.g. --json, --yes, --security) still pass through.
# `doctor-fix` / `doctor-fix-dry` are dry-run (no mutations); `doctor-fix-apply`
# mutates and still requires --yes in non-interactive contexts (CI/pipes).
doctor-fix doctor-fix-dry:
	@bash "$(CURDIR)/scripts/doctor/run.sh" --fix --dry-run $(ARGS)

doctor-fix-apply:
	@bash "$(CURDIR)/scripts/doctor/run.sh" --fix --apply $(ARGS)

# LUM-319 — CI parity: disposable lumogis-test (docker-compose.yml + docker-compose.test-doctor.yml;
# avoids docker-compose.test.yml include chain — GHA "orchestrator conflicts with imported resource"),
# then `make doctor ARGS="--json"` + jq shape check. Overwrites ./.env from config/test.env.example;
# backs up locally if needed. See scripts/doctor/README.md and .cursor/plans/LUM-319-doctor-ci-integration.plan.md.
compose-test-doctor:
	@REPO="$(CURDIR)"; \
	set -euo pipefail; \
	cleanup() { \
	  (cd "$$REPO" && export COMPOSE_PROJECT_NAME=lumogis-test COMPOSE_FILE=docker-compose.yml:docker-compose.test-doctor.yml && docker compose --env-file config/test.env.example down -v) || true; \
	}; \
	trap cleanup EXIT INT TERM; \
	cp -f "$$REPO/config/test.env.example" "$$REPO/.env"; \
	export COMPOSE_PROJECT_NAME=lumogis-test; \
	export COMPOSE_FILE=docker-compose.yml:docker-compose.test-doctor.yml; \
	cd "$$REPO"; \
	docker compose --env-file config/test.env.example up -d --wait --wait-timeout 480; \
	$(MAKE) --no-print-directory doctor ARGS="--json" > "$$REPO/doctor.json"; \
	jq -e '.version == 1 and (.checks | type == "array")' < "$$REPO/doctor.json"; \
	rm -f "$$REPO/doctor.json"; \
	trap - EXIT INT TERM; \
	cleanup

# LUM-753 — the membership rule for orchestrator/tests/integration/: a test
# there requires a real external backend. Two checks, run with every backend
# deliberately unreachable: nothing may PASS (anything that does never needed a
# backend), and nothing may be unconditionally skipped (a bare @pytest.mark.skip
# satisfies the first check vacuously — three such stubs lived there for months
# with skip reasons naming Postgres and FalkorDB and bodies containing `pass`).
#
# INVARIANT: this target must neutralise EVERY env var that can point a test at
# a live service. Miss one and the verdict depends on the operator's shell —
# FALKORDB_URL and LUMOGIS_INTEGRATION_POSTGRES_DSN are the two that bite,
# because two modules skip only when the former is *absent* and three connect
# via the latter rather than POSTGRES_*.
check-integration-hermeticity: check-pytest
	@cd "$(CURDIR)/orchestrator" && \
	  env -u FALKORDB_URL -u LUMOGIS_INTEGRATION_POSTGRES_DSN \
	      -u LUMOGIS_REQUIRE_INTEGRATION_DB -u LUMOGIS_REQUIRE_PAPERLESS_FIXTURE \
	    AUTH_ENABLED=false \
	    LUMOGIS_ASSERT_NO_HERMETIC_INTEGRATION=1 \
	    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=1 \
	    QDRANT_URL=http://127.0.0.1:1 \
	    PAPERLESS_BASE_URL=http://127.0.0.1:1 \
	    $(PYTHON) -m pytest tests/integration -q --tb=no -p no:cacheprovider

# LUM-309 — live paperless-ngx fixture. Brings up a throwaway paperless (plus
# its redis broker, and from Pass 2 a dedicated Postgres + Qdrant), then runs
# the two live modules against it. ~2 min warm; Step 0 measured 68s.
#
# THE PROJECT NAME IS A SAFETY MECHANISM, NOT A TIDINESS ONE. The trap runs
# `down -v`, which deletes volumes. Scoped to COMPOSE_PROJECT_NAME=
# lumogis-paperless-test it deletes only this fixture's volumes; under the
# default project name it would delete the developer's real dev-stack volumes —
# their household data — on the very first run. Do not remove the export, and
# do not reuse this recipe's teardown shape without it.
# check-pytest first, deliberately: without it a contributor whose $(PYTHON) is
# not the venv pays a ~60s container boot before failing on a bare
# "No module named pytest", with the trap tearing the evidence down behind it.
compose-test-paperless: check-pytest
	@REPO="$(CURDIR)"; \
	set -euo pipefail; \
	export COMPOSE_PROJECT_NAME=lumogis-paperless-test; \
	export COMPOSE_FILE=docker-compose.paperless-test.yml; \
	PAPERLESS_ADMIN_USER=admin; \
	PAPERLESS_ADMIN_PASSWORD="$$(openssl rand -hex 16 2>/dev/null || $(PYTHON) -c 'import secrets;print(secrets.token_hex(16))')"; \
	PAPERLESS_SECRET_KEY="$$(openssl rand -hex 32 2>/dev/null || $(PYTHON) -c 'import secrets;print(secrets.token_hex(32))')"; \
	export PAPERLESS_ADMIN_USER PAPERLESS_ADMIN_PASSWORD PAPERLESS_SECRET_KEY; \
	cleanup() { \
	  status=$$?; \
	  if [ "$$status" -ne 0 ]; then \
	    echo "--- paperless container logs (dumped before teardown destroys them) ---" >&2; \
	    (cd "$$REPO" && docker compose logs --tail 200 paperless) >&2 || true; \
	  fi; \
	  (cd "$$REPO" && docker compose down -v) || true; \
	  return $$status; \
	}; \
	trap cleanup EXIT INT TERM; \
	cd "$$REPO"; \
	docker compose up -d --wait --wait-timeout 420; \
	cd "$$REPO/orchestrator" && \
	  LUMOGIS_MIGRATIONS_DIR="$$REPO/postgres/migrations" \
	  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 \
	  POSTGRES_USER=lumogis POSTGRES_PASSWORD=lumogis-dev POSTGRES_DB=lumogis \
	  $(PYTHON) -m db_migrations; \
	cd "$$REPO/orchestrator" && \
	  AUTH_ENABLED=false \
	  LUMOGIS_REQUIRE_PAPERLESS_FIXTURE=1 \
	  LUMOGIS_REQUIRE_INTEGRATION_DB=1 \
	  PAPERLESS_BASE_URL=http://127.0.0.1:8010 \
	  PAPERLESS_ADMIN_USER="$$PAPERLESS_ADMIN_USER" \
	  PAPERLESS_ADMIN_PASSWORD="$$PAPERLESS_ADMIN_PASSWORD" \
	  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 \
	  POSTGRES_USER=lumogis POSTGRES_PASSWORD=lumogis-dev POSTGRES_DB=lumogis \
	  QDRANT_URL=http://127.0.0.1:6337 \
	  $(PYTHON) -m pytest tests/integration -v --tb=short -m paperless_live
	@$(MAKE) --no-print-directory coverage-matrix-check

# Admin-only Rescan of the configured library folders (LUM-884); no folder argument.
# Works as-is on an AUTH_ENABLED=false dev stack; with auth on, use the dashboard
# or add an admin Bearer header.
ingest:
	curl -s -X POST http://localhost:8000/ingest | python3 -m json.tool

# LUM-43 — compose policy (host Python + PyYAML + Docker Compose CLI).
# LUM-620 — every first-party compose file is passed as --core-file; only core files
# may define a service named in scripts/compose_core_allowlist.txt. Fixtures and
# capability overlays stay -f (non-core).
compose-policy-check-baseline:
	$(PYTHON) scripts/check_compose_policy.py --core-file docker-compose.yml

# compose-policy-check: mock overlay needs MOCK_CAPABILITY_SHARED_SECRET for `docker compose config`.
# Also validates docker-compose.ghcr.yml (CI publish path).

# Expect checker exit 1 (policy violation); make exit 0 means the guard caught the regression.
compose-policy-check-adversarial:
	@$(PYTHON) scripts/check_compose_policy.py --core-file docker-compose.yml -f docker-compose.test-policy-adversarial.yml; \
	ec=$$?; \
	if [ $$ec -eq 1 ]; then exit 0; fi; \
	echo "compose-policy-check-adversarial: expected policy violation (exit 1), got $$ec" >&2; \
	exit 1

compose-policy-check-adversarial-envfile:
	@$(PYTHON) scripts/check_compose_policy.py --core-file docker-compose.yml -f docker-compose.test-policy-adversarial-envfile.yml; \
	ec=$$?; \
	if [ $$ec -eq 1 ]; then exit 0; fi; \
	echo "compose-policy-check-adversarial-envfile: expected policy violation (exit 1), got $$ec" >&2; \
	exit 1

# LUM-613 — a COMMUNITY capability overlay must not obtain Core creds via env_file.
# Expect checker exit 1 (policy violation); make exit 0 means the guard caught it.
compose-policy-check-community:
	@$(PYTHON) scripts/check_compose_policy.py --core-file docker-compose.yml -f docker-compose.test-policy-community-capability.yml; \
	ec=$$?; \
	if [ $$ec -eq 1 ]; then exit 0; fi; \
	echo "compose-policy-check-community: expected policy violation (exit 1), got $$ec" >&2; \
	exit 1

# LUM-620 — a non-core overlay must not define an allowlisted service name
# (`backup`, `playwright`). Expect exit 1 AND a name-squat line for each service, so
# a different Pass A rule firing cannot satisfy the target. Pass A fails before
# `docker compose config`, so this needs no Docker.
compose-policy-check-name-squat:
	@out=$$($(PYTHON) scripts/check_compose_policy.py --core-file docker-compose.yml \
	  -f docker-compose.test-policy-name-squat.yml 2>&1); ec=$$?; \
	if [ $$ec -ne 1 ]; then \
	  echo "$$out" >&2; \
	  echo "compose-policy-check-name-squat: expected policy violation (exit 1), got $$ec" >&2; exit 1; \
	fi; \
	for svc in backup playwright; do \
	  if ! printf '%s\n' "$$out" | grep -q "name-squat: service '$$svc'"; then \
	    echo "$$out" >&2; \
	    echo "compose-policy-check-name-squat: no name-squat violation for '$$svc'" >&2; exit 1; \
	  fi; \
	done; \
	echo "compose-policy-check-name-squat: PASSED (overlay squatting backup + playwright rejected)"

# LUM-618 — Pass C (network membership). Two proofs:
#  (1) the adversarial violation fixture MUST exit 1 (proves Pass C actually
#      fires — guards against a silent no-op if the rendered networks shape ever
#      differs); make exit 0 means the guard caught it.
#  (2) the REAL RC render (base+test+public-rc-stack+egress overlay) MUST exit 0
#      with the mock on the isolated network — proving the reference deployment
#      is contained, not just a bespoke fixture (the R2 P0 guard).
compose-policy-check-egress:
	@python3 -c "import yaml" 2>/dev/null || \
	  python3 -m pip install -q -r scripts/requirements-compose-policy.txt
	@echo "==> Pass C fires on the violation fixture (expect exit 1)"
	@# No --core-file: the fixture is a non-core overlay (it defines no allowlisted name).
	@$(PYTHON) scripts/check_compose_policy.py \
	  -f docker-compose.test-policy-community-egress-violation.yml \
	  --community-service bad-community-cap; \
	ec=$$?; \
	if [ $$ec -ne 1 ]; then \
	  echo "compose-policy-check-egress: violation fixture expected exit 1, got $$ec" >&2; exit 1; \
	fi
	@echo "==> Pass C passes on the real RC render — mock must be contained (expect exit 0)"
	@# Do NOT merge docker-compose.test.yml here: its `include:` of the base file
	@# triggers GHA Compose "services.orchestrator conflicts with imported resource"
	@# (same class as LUM-319 / LUM-60 doctor + web-e2e overlays). Pass C only needs
	@# network topology from base + public-rc-stack + egress.
	@COMPOSE_PROFILES=community-egress MOCK_CAPABILITY_SHARED_SECRET=lumogis-ci-mock-capability-placeholder \
	  $(PYTHON) scripts/check_compose_policy.py \
	  --project-directory "$(CURDIR)" \
	  --core-file docker-compose.yml \
	  --core-file docker-compose.public-rc-stack.yml --core-file docker-compose.egress.yml \
	  --community-service lumogis-mock-capability
	@echo "compose-policy-check-egress: PASSED"

# LUM-618 — live container-network containment proof (Docker). Brings up the
# isolated network + Squid egress proxy + a community-probe, asserts the allowed
# host is reachable (spliced), a non-declared host is refused via a Squid deny,
# and a proxy-bypass has no route (incl. IPv6). Runs the PoC-validated Squid
# config (plan step 1). Requires Docker; ~1–2 min.
egress-containment-test: check-pytest
	$(PYTHON) -m pytest tests/integration/test_egress_containment.py -v --tb=short -m integration

# LUM-625 — email-IMAP cooperative-audit egress: proxy splices a declared-host
# CONNECT to 993 through to the real server, and refuses undeclared hosts /
# non-993 ports / non-Core sources. Allowed-vs-denied is read off the peer
# certificate, not the CONNECT status line, which under `ssl_bump peek` is 200
# either way (LUM-763). Proves the audit layer, NOT that a dual-homed Core cannot
# bypass the proxy (that is Layer 1). Requires Docker; ~1 min.
egress-imap-test:
	$(PYTHON) -m pytest tests/integration/test_email_imap_egress.py -v --tb=short -m integration

# LUM-811 — LUM-810 pre-main gate: live IMAP + p95 + adversarial parse + Playwright.
# Starts Greenmail when LUM811_IMAP_HOST is unset. Requires docker compose stack for e2e.
verify-lum-811:
	bash scripts/verify_lum_811.sh

# LUM-572 — Layer-1 scoped egress guard against a REAL container environment.
# build_allowlist derives from env vars AND DB state, and every other test supplies
# both itself, so the guard's inputs are otherwise untested: a compose overlay that
# renames a service or drops a variable silently yields an allowlist that is too
# narrow (blocks local traffic) or too wide (permits egress it should stop).
# Deliberately does NOT assert a route-level 503 — build_allowlist allowlists every
# configured model's own endpoint, so that test cannot be built honestly. Requires
# Docker; ~35s.
egress-guard-test:
	$(PYTHON) -m pytest tests/integration/test_egress_guard_compose.py -v --tb=short -m integration

# LUM-621 — allow file must match declared external_endpoints (mock RC fixture).
check-egress-acl-divergence:
	$(PYTHON) -m scripts.gen_capability_egress_acl --check \
		--id lumogis.mock.echo --endpoints example.com

# LUM-625 — the email-IMAP Layer-2 Squid ACL wiring must not be silently dropped
# (offline grep guard; complements the live make egress-imap-test).
check-email-imap-egress-wiring:
	$(PYTHON) scripts/check_email_imap_egress_wiring.py

# LUM-613 — LUM-613 files must stay tethered-free (tethered DiD deferred to LUM-619).
check-no-tethered-lum613:
	$(PYTHON) scripts/check_no_tethered_lum613.py

# ─── Docker-based CI (no running stack or local Python required) ──────────────

# LUM-29 — forbid legacy ``refresh_token_jti`` mentions in orchestrator prod modules.
auth-sessions-grep-guard:
	@$(PYTHON) scripts/check_refresh_token_jti_guard.py

# LUM-193 — optional pre-push mirror of the CI changelog path gate.
changelog-check:
	@scripts/check-changelog-touched.sh

release-doc-sync-check: ## main vs dev — CHANGELOG, capabilities, public-export templates
	@scripts/check-dev-release-doc-sync.sh

# LUM-226 — lumogis.ai static /capabilities + /changelog (Cloudflare Pages sibling repo).
LUMOGIS_SITE_ROOT ?= $(abspath ../lumogis-site/public)
render-site-pages: ## Regenerate lumogis-site capabilities + changelog HTML
	@test -d "$(LUMOGIS_SITE_ROOT)" || { echo "LUMOGIS_SITE_ROOT missing: $(LUMOGIS_SITE_ROOT)"; exit 1; }
	node scripts/render-lumogis-site-pages.mjs --site-out "$(LUMOGIS_SITE_ROOT)" \
	  --omit-unreleased --strip-ticket-ids

check-site-pages: check-pytest ## Dry-run renderer + pytest harness for site page generator
	node scripts/render-lumogis-site-pages.mjs --site-out "$(LUMOGIS_SITE_ROOT)" \
	  --omit-unreleased --strip-ticket-ids --dry-run
	$(PYTHON) -m pytest orchestrator/tests/test_render_lumogis_site_pages_script.py -q

# LUM-429 — TEST-COVERAGE-MATRIX format + feature-ids.json sync (Node; no network).
coverage-matrix-check:
	@node scripts/check-coverage-matrix.mjs

# Ratchet against new duplicate ADR numbers in docs/decisions/ (two ADRs both
# numbered 199 landed from parallel branches on 2026-08-02). Historical
# duplicates are baselined in scripts/adr-duplicate-baseline.txt.
adr-check:
	@bash scripts/check-adr-numbering.sh

# LUM-784 — core.fileMode=false makes git ignore exec bits, so a script committed
# from a local checkout lands as 100644 and fails with "permission denied" on a
# clean checkout. Reads modes from the index; sourced-only files are allowlisted
# in scripts/exec-bit-allowlist.txt.
exec-bit-check:
	@bash scripts/check-exec-bits.sh

# LUM-789 — report files origin/main changed that this tree does not have the change
# for, so RC-cycle CI fixes stop stranding on the release line. Needs a FULL clone
# (git fetch --unshallow) and a current origin/main; known dev-ahead paths are
# retired in scripts/main-dev-drift-baseline.txt. Run after a promotion goes green
# (/prepare-private-release-from-dev Step 10).
main-dev-drift-check:
	@bash scripts/check-main-dev-drift.sh

verify-no-telemetry:
	grep -r "posthog\|mixpanel\|amplitude" orchestrator/ && echo "FAIL: analytics library found" && exit 1 || echo "OK: no analytics libraries found"

# Run ruff inside the orchestrator container (no local Python needed).
# Dev deps (ruff) are installed on the fly; they are not in the production image.
compose-lint:
	COMPOSE_FILE=docker-compose.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm --entrypoint "" orchestrator \
	  sh -c 'pip install -q -r /project/orchestrator/requirements-dev.txt && \
	         ruff check /project/orchestrator/ /project/services/lumogis-graph/ && \
	         ruff format --check /project/orchestrator/ /project/services/lumogis-graph/ && \
	         python /project/scripts/check_refresh_token_jti_guard.py'

compose-policy-check:
	@python3 -c "import yaml" 2>/dev/null || \
	  python3 -m pip install -q -r scripts/requirements-compose-policy.txt
	MOCK_CAPABILITY_SHARED_SECRET=lumogis-ci-mock-capability-placeholder \
	  $(PYTHON) scripts/check_compose_policy.py --core-file docker-compose.yml -f docker-compose.mock-capability.yml
	@python3 scripts/check_compose_policy.py \
	  --project-directory "$(CURDIR)" \
	  --core-file docker-compose.yml \
	  --core-file docker-compose.ghcr.yml
	@# LUM-980: the compose file users install from, generated with its .env.
	@$(MAKE) --no-print-directory release-compose RELEASE_COMPOSE_OUT=$(CURDIR)/dist/release-policy
	@cp dist/release-policy/example.env dist/release-policy/.env
	@python3 scripts/check_compose_policy.py \
	  --project-directory "$(CURDIR)/dist/release-policy" \
	  --core-file "$(CURDIR)/dist/release-policy/docker-compose.yml"
	@rm -rf dist/release-policy

# LUM-980: the two files a household installs from (docker-compose.yml and
# example.env), as the release-assets workflow attaches them to a GitHub Release.
RELEASE_COMPOSE_OUT ?= dist/release
RELEASE_VERSION ?= dev
release-compose: ## Generate the release docker-compose.yml + example.env into dist/release (LUM-980)
	@python3 scripts/release/build_release_compose.py --check --version "$(RELEASE_VERSION)" --out "$(RELEASE_COMPOSE_OUT)"

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

caddy-keepalive-check: ## LUM-875 — live: POSTs through Caddy after ~5 s idle must not 502 (throwaway containers; ARGS="--around 75" etc.)
	scripts/check-caddy-upstream-keepalive.sh $(ARGS)

graph-relates-to-merge-policy-check: ## LUM-208 — AST-aware scan for invalid RELATES_TO MERGE shapes
	$(PYTHON) scripts/check_graph_relates_to_merge_policy.py

m1-compat-with-retry: ## Live FalkorDB compat gate — e.g. FALKORDB_URL=redis://127.0.0.1:6380 RUN_M1_COMPAT=1 (optional FALKORDB_HOST_PORT if not 6380); one retry on flake
	cd orchestrator && (RUN_M1_COMPAT=1 $(PYTHON) -m pytest tests/premium/test_graph_writer.py::TestFalkorDBCompatGate -q || (sleep 2 && RUN_M1_COMPAT=1 $(PYTHON) -m pytest tests/premium/test_graph_writer.py::TestFalkorDBCompatGate -q))

# NOTE: Intended for release/export-shaped checkouts (see scripts/create-upstream-export-tree.sh).
verify-public-rc: ## RC gate (smoke) — run before /publish-private-main-to-public
	@echo "==> verify-public-rc (smoke)"
	scripts/check-main-hygiene.sh
	$(MAKE) compose-policy-check
	$(MAKE) graph-relates-to-merge-policy-check
	$(MAKE) compose-test
	@# The graph service's tests in its own image: CI runs them in the checkout
	@# layout only, so a test that broke in the image shipped in 0.12.0 unseen.
	$(MAKE) compose-test-kg
	@if [ -z "$${VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK:-}" ]; then \
	  $(MAKE) web-codegen-check; \
	else \
	  echo "WARN: web-codegen-check skipped (VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK set)"; \
	fi
	$(MAKE) web-lint
	$(MAKE) web-test
	$(MAKE) web-build
	@# LUM-313 — run the offline OpenAPI breaking-change gate locally so the RC
	@# gate is closer to "runnable proof" (ADR-061 deferred this from LUM-303).
	@# oasdiff is a Go dev tool that may be absent on dev machines; CI
	@# (.github/workflows/ci.yml openapi-check job) is the binding gate, so a
	@# missing oasdiff degrades to a documented WARN. Set
	@# VERIFY_PUBLIC_RC_REQUIRE_OPENAPI_BREAKING=1 to make it a hard local gate.
	@if command -v oasdiff >/dev/null 2>&1; then \
	  echo "==> openapi-breaking-check (RC gate, LUM-313)"; \
	  $(MAKE) openapi-breaking-check; \
	elif [ "$${VERIFY_PUBLIC_RC_REQUIRE_OPENAPI_BREAKING:-}" = "1" ]; then \
	  echo "ERROR: oasdiff not on PATH and VERIFY_PUBLIC_RC_REQUIRE_OPENAPI_BREAKING=1 (LUM-313)" >&2; \
	  echo "       install Go 1.26+ then: go install github.com/oasdiff/oasdiff@v1.15.2" >&2; \
	  exit 1; \
	else \
	  echo "WARN: openapi-breaking-check skipped — oasdiff not on PATH (LUM-313)."; \
	  echo "      Binding gate is CI (ci.yml openapi-check job). Set"; \
	  echo "      VERIFY_PUBLIC_RC_REQUIRE_OPENAPI_BREAKING=1 to require it locally."; \
	fi
	@if [ "$${VERIFY_PUBLIC_RC_FORCE_INTEGRATION:-}" = "1" ]; then \
	  scripts/integration-public-rc.sh full-cycle; \
	elif [ "$${VERIFY_PUBLIC_RC_SKIP_INTEGRATION:-}" = "1" ]; then \
	  echo "WARN: integration step skipped (VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1) — only use this on dev machines with live production stacks"; \
	else \
	  scripts/integration-public-rc.sh full-cycle; \
	fi
	scripts/create-upstream-export-tree.sh
	scripts/check-public-export.sh /tmp/lumogis-upstream-export
	@echo "==> verify-public-rc PASSED"

# Runs verify-public-rc with VERIFY_PUBLIC_RC_FORCE_INTEGRATION=1 so integration
# always executes even if VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1 is set in the environment.
verify-public-rc-full: ## Full RC gate — includes e2e and optional graph parity
	@echo "==> verify-public-rc-full"
	@VERIFY_PUBLIC_RC_FORCE_INTEGRATION=1 $(MAKE) verify-public-rc
	@if [ -z "$${VERIFY_PUBLIC_RC_SKIP_WEB_E2E:-}" ]; then \
	  COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml \
	    docker compose --env-file config/test.env.example build lumogis-web; \
	  scripts/integration-public-rc.sh gate-start; \
	  set -a && . config/test.env.example && set +a && \
	  $(MAKE) web-e2e-prove || { ec=$$?; scripts/integration-public-rc.sh gate-end || true; exit $$ec; }; \
	  scripts/integration-public-rc.sh gate-end; \
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
# These targets pip install into the image, so they keep root (LUM-887).
compose-test:
	COMPOSE_FILE=docker-compose.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm -e LUMOGIS_RUN_UID=0 -e AUTH_ENABLED=false -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements.txt && pip install -q -r requirements-dev.txt && python -m pytest tests -x -q"

# Stack-control unit tests (mounts stack-control/; dev deps from stack-control/requirements-dev.txt).
compose-test-stack-control:
	COMPOSE_FILE=docker-compose.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm -e LUMOGIS_RUN_UID=0 -v $(PWD)/stack-control:/sc:rw orchestrator sh -c \
	  "pip install -q -r /sc/requirements-dev.txt && cd /sc && python -m pytest test_main.py -q"

# Run integration tests (requires stack to be up; mounts repo-root tests/ into container).
# Uses the FalkorDB overlay so graph integration tests can run against a live instance.
# If FalkorDB is not in COMPOSE_FILE, graph tests are skipped automatically.
compose-test-integration:
	COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose up -d falkordb postgres qdrant
	COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml \
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6335} \
	docker compose run --rm \
	  -e LUMOGIS_RUN_UID=0 \
	  -e GRAPH_BACKEND=falkordb \
	  -e FALKORDB_URL=redis://falkordb:6379 \
	  -v $(PWD)/tests:/integration-tests:ro \
	  orchestrator \
	  sh -c "pip install -q -r /project/orchestrator/requirements-dev.txt && cd /project/orchestrator && PYTHONPATH=/project/services/lumogis-graph:. python -m pytest /integration-tests/integration tests/integration/test_entity_edges_reconcile_falkordb_live.py tests/integration/test_entity_merge_edges_live.py tests/integration/test_document_shared_entity_cascade_live.py tests/integration/test_document_shared_graph_recall_live.py tests/integration/test_document_entity_unshare_live.py tests/integration/test_document_reingest_entity_retraction_live.py -v --tb=short -m 'integration and not slow and not manual'"

compose-test-temporal-compose:
	COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml \
	  docker compose --env-file config/test.env.example run --rm --no-deps \
	  --entrypoint sh \
	  -v $(PWD)/tests:/integration-tests:ro \
	  -e LUMOGIS_GRAPH_HEALTH_URL=http://lumogis-graph:8001/health \
	  -e LUMOGIS_API_URL=http://orchestrator:8000 \
	  orchestrator \
	  -c "pip install -q -r /project/orchestrator/requirements-dev.txt && python -m pytest /integration-tests/integration/test_temporal_compose.py -v --tb=short"

# LUM-185 — DR backup sidecar (operator targets).
backup:
	docker compose run --rm backup /scripts/backup/backup.sh run

backup-prune:
	docker compose run --rm backup /scripts/backup/backup.sh prune

backup-verify:
	@if [ -n "$(ARGS)" ]; then \
	  docker compose run --rm backup /scripts/backup/verify.sh $(ARGS) --rewrite-manifest; \
	else \
	  docker compose run --rm backup sh -c 'latest=$$(ls -1t /backups/snapshots 2>/dev/null | grep -v "^\.tmp$$" | head -1); test -n "$$latest"; /scripts/backup/verify.sh "/backups/snapshots/$$latest" --rewrite-manifest'; \
	fi

SNAPSHOT ?=
restore:
	docker compose run --rm -it backup /scripts/backup/restore.sh $(SNAPSHOT) --yes

# --- Update / lifecycle (LUM-187) ---
migrate-dry-run: ## Preview pending DB migrations without applying them (LUM-187)
	docker compose run --rm orchestrator python3 /app/db_migrations.py --dry-run

update: ## Update Lumogis: pull images, restart, apply migrations, health-check (LUM-187)
	bash scripts/update/update.sh

rollback: ## Roll back to the previous images captured by `make update` (needs recent backup) (LUM-187)
	bash scripts/update/rollback.sh

compose-test-backup:
	QDRANT_HOST_PORT=$${QDRANT_HOST_PORT:-6336} bash scripts/integration-backup-roundtrip.sh

test-backup-retention:
	bash tests/unit/test_backup_retention.sh

test-backup-bgsave-wait:
	bash tests/unit/test_backup_bgsave_wait.sh

# LUM-892 — needs rsync + sha256sum on the host (apt install rsync)
test-backup-files:
	bash tests/unit/test_backup_files.sh

# Phase 5 dev-only second capability (not part of default compose); see services/lumogis-mock-capability/README.md
mock-capability-test:
	cd services/lumogis-mock-capability && $(PYTHON) -m pip install -q -r requirements-dev.txt && $(PYTHON) -m pytest tests -q

# LUM-377 — summary-first test wrappers (see scripts/debug/README.md).
test-list:
	@./scripts/debug/cli.sh list

debug:
	@./scripts/debug/cli.sh debug

# ─── Developer tools (requires local venv) ───────────────────────────────────

# Dependency CVE audit (npm audit + pip-audit). Free/local only; needs network for advisory DBs.
# Bootstrap: creates .venv-audit/ when pip-audit is not on PATH (gitignored).
# Optional env: LUMOGIS_DEVTOOLS=/path/to/lumogis-devtools (default ../lumogis-devtools).
# AUDIT_SKIP_NPM=1 or AUDIT_SKIP_PIP=1 to scan one ecosystem only.
audit-local:
	bash scripts/audit_local.sh

# LUM-190 — advisory Bandit on orchestrator/ (same flags as CI). Uses a local venv (PEP 668–safe).
# OWASP ZAP baseline (operator, one-shot): mount a writable dir as /zap/wrk, then e.g.
#   docker run --rm -v "$PWD/tmp-zap-wrk:/zap/wrk:rw" ghcr.io/zaproxy/zaproxy:stable \
#     zap-baseline.py -t "https://<rc-base-url>/" -J zap-baseline-2026.json -I
# Do not use deprecated Docker Hub owasp/zap2docker-stable; pin ghcr.io/zaproxy/zaproxy by digest for reproducibility.
bandit-check:
	@test -x "$(CURDIR)/.venv-bandit-check/bin/bandit" || ( $(PYTHON) -m venv "$(CURDIR)/.venv-bandit-check" && "$(CURDIR)/.venv-bandit-check/bin/pip" install -q --require-hashes -r scripts/requirements-security-audit.txt )
	-@"$(CURDIR)/.venv-bandit-check/bin/bandit" -r orchestrator/ -ll -ii

# LUM-318 — RC-target OWASP ZAP baseline + update audit artefacts (after release / verify-public-rc-full).
zap-rc-baseline-lum318:
	bash scripts/zap-rc-baseline-lum318.sh

# npm audit fix must run under clients/lumogis-web (package-lock.json lives there; repo root has none).
web-audit-fix:
	cd clients/lumogis-web && npm audit fix

# Requires local venv (contributors only)
# LUM-946 — services/lumogis-graph/ is linted with Core's settings (root
# pyproject.toml); it had drifted while only orchestrator/ was checked.
lint: check-pytest
	ruff check orchestrator/ services/lumogis-graph/
	ruff format --check orchestrator/ services/lumogis-graph/
	@$(PYTHON) scripts/check_refresh_token_jti_guard.py

# LUM-321 — fail fast when pytest is missing from $(PYTHON)'s environment.
check-pytest:
	@$(PYTHON) -c "import pytest" 2>/dev/null || (echo "make test: pytest not available for $(PYTHON). See CONTRIBUTING.md — Running tests (local venv)." >&2; exit 2)

# Requires local venv — see CONTRIBUTING.md (orchestrator + stack-control requirements-dev.txt).
# Orchestrator route tests use unauthenticated TestClient with the synthetic `default`
# user when `require_user` no-ops — that requires AUTH_ENABLED=false unless every test
# supplies a bearer token. Host shells often export AUTH_ENABLED=true from compose.
test: check-pytest
	$(MAKE) test-backup-retention
	$(MAKE) test-backup-files
	$(MAKE) test-backup-bgsave-wait
	$(MAKE) test-env-unit
	cd orchestrator && AUTH_ENABLED=false $(PYTHON) -m pytest -x -q
	cd stack-control && $(PYTHON) -m pytest test_main.py -q
	$(MAKE) --no-print-directory check-integration-hermeticity

# LUM-894 — offline test environment (scripts/test-env.sh). Needs Docker only:
# no local venv or node_modules. Tier 1 = test-runner image, no network;
# tier 2 = plus throwaway Postgres/Qdrant/FalkorDB, removed after every run.
test-env-unit:
	bash tests/unit/test_test_env.sh

test-env-build:
	scripts/test-env.sh build

test-env-test:
	scripts/test-env.sh run --tier 1 -- make test

# Every orchestrator/tests/integration module against real backends, with
# LUMOGIS_REQUIRE_INTEGRATION_DB=1 (modules using the LUM-718 guard fail rather than skip).
test-env-integration:
	scripts/test-env.sh run --tier 2 -- bash -c 'cd orchestrator && python -m pytest tests/integration -q -rs'

# LUM-542: recall latency at bank scale (1k/5k/10k synthetic memories) against
# throwaway tier-2 Postgres + Qdrant; no network model. Not part of `make test`.
#   make bench-recall BENCH_RECALL_MAX_P95_MS=200   # exit 1 if a p95 is above 200ms
#   make bench-recall BENCH_RECALL_ARGS="--modes fusion --sizes 10000"
BENCH_RECALL_SIZES ?= 1000,5000,10000
BENCH_RECALL_QUERIES ?= 200
BENCH_RECALL_MAX_P95_MS ?=
BENCH_RECALL_JSON ?= target/bench-recall/latest.json
BENCH_RECALL_ARGS ?=
bench-recall:
	scripts/test-env.sh run --tier 2 -- python3 orchestrator/benchmarks/recall_bench.py \
	  --sizes $(BENCH_RECALL_SIZES) --queries $(BENCH_RECALL_QUERIES) \
	  --json-out $(BENCH_RECALL_JSON) \
	  $(if $(BENCH_RECALL_MAX_P95_MS),--max-p95-ms $(BENCH_RECALL_MAX_P95_MS)) \
	  $(BENCH_RECALL_ARGS)

# Requires local venv (contributors only). Uses orchestrator venv/deps.
test-integration: check-pytest
	cd orchestrator && $(PYTHON) -m pytest ../tests/integration -v --tb=short -m "integration and not slow and not restart_e2e"

# Includes slow cases (e.g. wait for signal poll). Still excludes ``restart_e2e``
# (LUM-400) — that lane is ``make e2e-ingest-restart`` only; it recreates the
# orchestrator and rewrites ``.env``.
test-integration-full:
	cd orchestrator && $(PYTHON) -m pytest ../tests/integration -v --tb=short -m "integration and not restart_e2e"

# LUM-400 — disruptive live-stack proof: ingest_paths change + POST /settings/restart.
# Requires Docker and scripts/integration-public-rc.sh RC stack (~3–5 min). Not part of
# default test-integration or verify-public-rc.
# OLLAMA_SKIP_WAIT=true keeps every orchestrator --force-recreate fast and deterministic
# (no on-boot Ollama wait/model pull); the proof is file_index_count, not embeddings.
e2e-ingest-restart: export OLLAMA_SKIP_WAIT = true
e2e-ingest-restart:
	scripts/integration-public-rc.sh gate-start
	scripts/integration-public-rc.sh restart-e2e-pytest
	scripts/integration-public-rc.sh gate-end

# LUM-929 — live proof of the LUM-884 ingest-ownership change: test_graph.py and
# test_integration_flow.py on the seeded RC stack (AUTH_ENABLED=true, a real
# INBOX_OWNER_USER_ID, GRAPH_MODE=service, the extraction model pulled and warmed),
# failing on any skip. About 15-40 min on CPU (LLM waits). Opt-in: NOT part of
# verify-public-rc or test-integration. The RC port preflight covers only Core
# (ORCHESTRATOR_HOST_PORT, default 8000) and the RC Qdrant (6335): a Qdrant on 6334 is
# left alone, but a daily Core publishing 8000 is still stopped by gate-start (existing
# behaviour). Evidence lands in .cache/lum929/.
# No OLLAMA_SKIP_WAIT here, unlike e2e-ingest-restart: this lane needs the models.
# Line 1 refuses (exit 2) before any container starts or .env is touched. Line 2 is one
# shell line so gate-end runs even when gate-start fails.
e2e-ingest-live:
	scripts/integration-public-rc.sh ingest-live-preflight
	set +e; scripts/integration-public-rc.sh gate-start && scripts/integration-public-rc.sh ingest-live-pytest; ec=$$?; scripts/integration-public-rc.sh gate-end; exit $$ec

# LUM-808 — see the LUMOGIS_GPU block at the top of this file. The overlay is
# included only when the NVIDIA container runtime is actually present, so a
# GPU-less contributor gets a working stack and a one-line notice instead of a
# device-driver error. `LUMOGIS_GPU=1` restores the old hard requirement.
dev:
	@set -euo pipefail; \
	if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then \
	  have_gpu=1; \
	else \
	  have_gpu=0; \
	fi; \
	gpu_file=""; \
	case "$(LUMOGIS_GPU)" in \
	  1) \
	    if [ "$$have_gpu" != 1 ]; then \
	      echo "make dev: LUMOGIS_GPU=1 but Docker has no 'nvidia' runtime." >&2; \
	      echo "  Install nvidia-container-toolkit and load the driver (docs/guides/gpu-setup.md)," >&2; \
	      echo "  or run without the GPU overlay: LUMOGIS_GPU=0 make dev" >&2; \
	      exit 1; \
	    fi; \
	    gpu_file="-f docker-compose.gpu.yml"; \
	    echo "make dev: GPU overlay ON (LUMOGIS_GPU=1)"; \
	    ;; \
	  0) \
	    echo "make dev: GPU overlay OFF (LUMOGIS_GPU=0) — Ollama on CPU"; \
	    ;; \
	  *) \
	    if [ "$$have_gpu" = 1 ]; then \
	      gpu_file="-f docker-compose.gpu.yml"; \
	      echo "make dev: NVIDIA runtime detected — GPU overlay ON"; \
	    else \
	      echo "make dev: no NVIDIA runtime in Docker — starting WITHOUT the GPU overlay (Ollama on CPU)."; \
	      echo "  This is supported; the GPU is an optimisation. See docs/guides/gpu-setup.md to enable it,"; \
	      echo "  or set LUMOGIS_GPU=1 to make its absence a hard error."; \
	    fi; \
	    ;; \
	esac; \
	bash scripts/update/rescue-captures.sh; \
	bash scripts/update/rescue-stop-entities.sh; \
	set -x; \
	docker compose -f docker-compose.yml $$gpu_file -f docker-compose.dev.yml up --build --pull always

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
	  lumogis-graph:test python -m pytest tests -x -q -m "not integration"

# LUM-567 — FalkorDB-backed temporal integration (requires lumogis-test network).
compose-test-kg-integration: test-kg-image
	docker run --rm --network $${COMPOSE_PROJECT_NAME:-lumogis-test}_default \
	  -e KG_INTEGRATION_FALKORDB=1 \
	  -e GRAPH_BACKEND=falkordb \
	  -e FALKORDB_URL=redis://falkordb:6379 \
	  -e POSTGRES_HOST=postgres \
	  -e POSTGRES_PORT=5432 \
	  -e POSTGRES_USER=lumogis \
	  -e POSTGRES_PASSWORD=lumogis-dev \
	  -e POSTGRES_DB=lumogis \
	  -e KG_ALLOW_INSECURE_WEBHOOKS=true \
	  -e KG_SCHEDULER_ENABLED=false \
	  -e LOG_LEVEL=ERROR \
	  lumogis-graph:test python -m pytest tests/test_temporal_integration.py -v --tb=short -m integration

# LUM-567 — full P1 slice: KG unit + FalkorDB integration + premium temporal + live eval.
compose-test-temporal-lum567: compose-test-kg compose-test-kg-integration compose-test-temporal-compose
	$(MAKE) compose-test-temporal-eval

compose-test-temporal-eval:
	COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml \
	  docker compose --env-file config/test.env.example run --rm --no-deps \
	  --entrypoint sh \
	  -v $(PWD):/project \
	  -e LUMOGIS_TEMPORAL_EVAL=1 \
	  -e LUMOGIS_FF_TEMPORAL_KG=true \
	  -e OLLAMA_URL=http://ollama:11434 \
	  -e MODELS_CONFIG=/project/orchestrator/tests/fixtures/models.temporal-eval.yaml \
	  -e GRAPH_MODE=inprocess \
	  orchestrator \
	  -c "pip install -q -r /project/orchestrator/requirements.txt && pip install -q -r /project/orchestrator/requirements-dev.txt && cd /project/orchestrator && PYTHONPATH=/project/services/lumogis-graph:/project/orchestrator python -m pytest tests/premium/temporal_eval/test_contradiction_eval.py::test_contradiction_eval_runs_and_emits_report tests/premium/test_temporal_pipeline.py -q --tb=short"

# Local-venv variant for contributors with a KG-side venv set up.
test-kg:
	cd services/lumogis-graph && $(PYTHON) -m pytest -x -q

# GRAPH_MODE parity test: ingests the same fixture corpus under
# `GRAPH_MODE=inprocess` and `GRAPH_MODE=service` and asserts the
# resulting FalkorDB snapshots are identical. Slow (boots/tears down
# the full stack twice) — not part of the default `test-integration`
# target. Requires Docker. The test itself self-skips if `docker` is
# not on PATH so contributors without Docker can still collect it.
#
# LUM-929 — runs in its own compose project (lumogis-parity) on its own host ports and
# never touches another stack's volumes. The checkout's .env must be the test template
# (a fresh worktree gets one here); the test refuses to run otherwise. Skips fail the run.
test-graph-parity:
	test -f .env || cp config/test.env.example .env
	mkdir -p .cache/lum929
	cd orchestrator && $(PYTHON) -m pytest \
	  ../tests/integration/test_graph_parity.py -v --tb=short -rs \
	  --junitxml $(CURDIR)/.cache/lum929/parity-junit.xml \
	  -m 'integration and slow and not manual'
	python3 scripts/junit_skip_guard.py .cache/lum929/parity-junit.xml --min-tests 1

# ─── Lumogis Web (clients/lumogis-web/) ──────────────────────────────────────
# Phase 1 Pass 1.1 introduced the React + TypeScript SPA. These targets run
# everything locally (npm + node ≥ 20). CI mirrors them in clients/lumogis-web/.

# LUM-252 — lockfile-pinned install (parity with lumogis-web Dockerfile / CI npm ci).
web-install:
	cd clients/lumogis-web && npm ci

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

# LUM-94 — plain alias for discoverability / CI job name (same as web-codegen-check).
openapi-check: web-codegen-check

# LUM-302 — semantic breaking-change diff on the committed OpenAPI snapshot (oasdiff CLI).
# Requires Go 1.26+ on PATH (oasdiff v1.15.2 module constraint) plus:
#   go install github.com/oasdiff/oasdiff@v1.15.2
# (pin must match the go install line in .github/workflows/ci.yml openapi-check job).
openapi-breaking-check:
	@command -v oasdiff >/dev/null 2>&1 || (echo "openapi-breaking-check: oasdiff not on PATH. Install Go 1.26+ then: go install github.com/oasdiff/oasdiff@v1.15.2" >&2; exit 2)
	bash .github/scripts/openapi-breaking-check.sh

# LUM-224 / LUM-253 — fail if Dockerfile drops lockfile COPY, npm ci, or BuildKit syntax.
web-dockerfile-check:
	@grep -qF 'COPY package.json package-lock.json' clients/lumogis-web/Dockerfile \
	  || (echo "web-dockerfile-check: clients/lumogis-web/Dockerfile must COPY package.json package-lock.json" >&2; exit 1)
	@grep -qE '^[[:space:]]*RUN\b.*\bnpm ci\b' clients/lumogis-web/Dockerfile \
	  || (echo "web-dockerfile-check: clients/lumogis-web/Dockerfile must RUN npm ci" >&2; exit 1)
	@grep -qF '# syntax=docker/dockerfile:1' clients/lumogis-web/Dockerfile \
	  || (echo "web-dockerfile-check: clients/lumogis-web/Dockerfile must declare # syntax=docker/dockerfile:1 for BuildKit cache mount" >&2; exit 1)
	@grep -qE '^[[:space:]]*RUN\b.*\bnpm install\b' clients/lumogis-web/Dockerfile \
	  && (echo "web-dockerfile-check: clients/lumogis-web/Dockerfile must not RUN npm install" >&2; exit 1) \
	  || true

# LUM-274 — static analysis for web-docker-build path gate (CI parity).
shellcheck-web-docker-build-paths:
	@command -v shellcheck >/dev/null 2>&1 || (echo "shellcheck-web-docker-build-paths: shellcheck not on PATH. Install e.g. apt install shellcheck" >&2; exit 2)
	shellcheck .github/scripts/web-docker-build-paths.sh

# LUM-444 — static analysis for remaining CI *-paths.sh gates (CI parity).
shellcheck-ci-paths:
	@command -v shellcheck >/dev/null 2>&1 || (echo "shellcheck-ci-paths: shellcheck not on PATH. Install e.g. apt install shellcheck" >&2; exit 2)
	shellcheck .github/scripts/web-e2e-paths.sh
	shellcheck .github/scripts/web-checks-paths.sh
	shellcheck .github/scripts/openapi-check-paths.sh
	shellcheck .github/scripts/doctor-integration-paths.sh
	shellcheck .github/scripts/backup-integration-paths.sh
	shellcheck .github/scripts/test-backup-integration-paths.sh
	shellcheck .github/scripts/security-audit-paths.sh
	shellcheck .github/scripts/test-security-audit-paths.sh

# LUM-254 — same image build CI exercises (requires Docker; run from repo root).
web-docker-build:
	docker compose build lumogis-web

web-test:
	cd clients/lumogis-web && npm test

web-lint:
	cd clients/lumogis-web && npm run lint

web-build:
	cd clients/lumogis-web && npm run build

web-dev:
	cd clients/lumogis-web && npm run dev

# LUM-430 — AGPL household search overlay (`clients/lumogis-search/`). Public export includes this tree.
# Requires Node 20+, Rust stable, OS webview deps (see clients/lumogis-search/README.md).
search-dev:
	cd clients/lumogis-search && npm ci && npm run build && npm run tauri:dev

search-build search-build-client:
	cd clients/lumogis-search && npm ci && npm run build && npm run tauri:build

# LUM-292 — MCP stdio bridge for Cursor (`clients/lumogis-mcp/`). Public export includes this tree.
lumogis-cursor-install:
	bash scripts/install-cursor-mcp.sh

test-lumogis-mcp:
	cd clients/lumogis-mcp && $(PYTHON) -m pip install -q -e '.[dev]' && $(PYTHON) -m pytest -q

# LUM-299: Cursor integration smoke (in-process breadth + stdio slice; no Docker).
# Sets LUMOGIS_CURSOR_INTEGRATION=1 so the p95 recall gate runs (~220 MCP calls).
test-cursor-integration:
	cd orchestrator && LUMOGIS_CURSOR_INTEGRATION=1 $(PYTHON) -m pytest -q tests/test_cursor_integration.py
	$(MAKE) test-lumogis-mcp

# LUM-299 opt-in: real Postgres+Qdrant stack (lumogis-test compose); hard p95 < 200ms.
# Prerequisites: full lumogis-test stack up, `make seed-cursor-integration-fixture`,
# then export LUMOGIS_CURSOR_INTEGRATION_MCP_TOKEN (or `make prove-cursor-integration-full`).
test-cursor-integration-full:
	cd orchestrator && LUMOGIS_CURSOR_INTEGRATION_FULL=1 $(PYTHON) -m pytest -q tests/test_cursor_integration_full.py

# LUM-540: seed coding_bank.json into lumogis-test (COMPOSE_PROJECT_NAME=lumogis-test).
seed-cursor-integration-fixture:
	bash scripts/seed-cursor-integration-fixture.sh

prove-cursor-integration-full: seed-cursor-integration-fixture
	@set -a && . ai-workspace/mcp/cursor-integration-full.env && set +a && \
	  $(MAKE) test-cursor-integration-full

# Optional maintainer extension makefiles (`Makefile.<name>.mk` at the repo root), when present.
-include $(sort $(wildcard Makefile.*.mk))

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

# Record the launch demo GIF (LUM-181): scripted two-user household-KB flow
# (admin upload -> share -> member search + document-chat) -> .webm -> GIF.
# Requires a running stack + admin creds (LUMOGIS_WEB_SMOKE_EMAIL/_PASSWORD) and
# member creds (DEMO_MEMBER_EMAIL/DEMO_MEMBER_PASSWORD), plus ffmpeg for the GIF.
# NOT part of CI. Runbook: clients/lumogis-web/tests/e2e/demo/README.md
web-demo:
	cd clients/lumogis-web && npx playwright test -c playwright.demo.config.ts
	cd clients/lumogis-web && ./scripts/demo-to-gif.sh test-results/demo/video docs/assets/demo.gif

# Labelled PNGs of main Lumogis Web screens → branding/screenshots/
web-screenshots:
	cd clients/lumogis-web && npx playwright test -c playwright.screenshots.config.ts

# Opt-in Ollama mutation Playwright (LUM-450). Requires full stack (docker compose up -d
# including ollama), smoke creds, LUMOGIS_E2E_EXPECT_ADMIN=1 and LUMOGIS_E2E_EXPECT_OLLAMA=1.
# NOT part of web-e2e-prove or verify-public-rc-full (ADR-064).
web-e2e-ollama-prove:
	cd clients/lumogis-web && \
	  E2E_REQUIRE_CREDS=1 \
	  LUMOGIS_E2E_EXPECT_ADMIN=1 \
	  LUMOGIS_E2E_EXPECT_OLLAMA=1 \
	  npx playwright test admin_ollama_mutations capture-inbox-archive-live --workers=1

# LUM-402 — overlay GUI E2E (WebdriverIO + tauri-driver) for the Lumogis Search Tauri
# overlay. Linux + xvfb only (WebKitGTK WebDriver); needs webkit2gtk-driver + Rust + Node.
# overlay-e2e: 5 MVP scenarios with `invoke` mocked (no Docker). See ADR-110.
# overlay-e2e-smoke: one live login+search round-trip — requires the RC compose Core up
# and OVERLAY_E2E_SMOKE_EMAIL / OVERLAY_E2E_SMOKE_PASSWORD in the environment.
overlay-e2e:
	cd clients/lumogis-search && xvfb-run -a npm run e2e

overlay-e2e-smoke:
	cd clients/lumogis-search && OVERLAY_E2E_SMOKE=1 xvfb-run -a npm run e2e:smoke

# Requires stack up (docker compose up -d). Uses the orchestrator image (pytest+httpx)
# and fetches the Caddy service at http://caddy (set LUMOGIS_WEB_BASE_URL to override).
# Skips if Caddy is unreachable unless LUMOGIS_CADDY_HEADER_PROVE=1 (then fails).
web-caddy-headers:
	docker compose run --rm -e LUMOGIS_RUN_UID=0 -e LUMOGIS_WEB_BASE_URL=$${LUMOGIS_WEB_BASE_URL:-http://caddy} \
	  -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && python -m pytest ../tests/integration/test_caddy_security_headers.py -m integration -q"

# Same as web-caddy-headers but fails when the front door is down (for automation).
web-caddy-headers-prove:
	docker compose run --rm -e LUMOGIS_RUN_UID=0 -e LUMOGIS_CADDY_HEADER_PROVE=1 \
	  -e LUMOGIS_WEB_BASE_URL=$${LUMOGIS_WEB_BASE_URL:-http://caddy} \
	  -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && python -m pytest ../tests/integration/test_caddy_security_headers.py -m integration -q"

# Live: a spoofed X-Forwarded-For through Caddy must not mint new rate-limit buckets (LUM-887).
caddy-client-ip-prove: ## Live: spoofed X-Forwarded-For through Caddy does not mint new rate-limit buckets (LUM-887)
	docker compose run --rm -e LUMOGIS_RUN_UID=0 -e LUMOGIS_CADDY_CLIENT_IP_PROVE=1 \
	  -e LUMOGIS_WEB_BASE_URL=$${LUMOGIS_WEB_BASE_URL:-http://caddy} \
	  -w /project/orchestrator orchestrator sh -c \
	  "pip install -q -r requirements-dev.txt && python -m pytest ../tests/integration/test_caddy_client_ip.py -m integration -q"

# Static: the Caddyfile adapts, and LUMOGIS_CADDY_TRUSTED_PROXIES reaches trusted_proxies (LUM-887).
caddy-config-check: ## Static: the Caddyfile adapts, and LUMOGIS_CADDY_TRUSTED_PROXIES reaches trusted_proxies (LUM-887)
	bash scripts/check-caddy-config.sh

# ─── Demo helpers ─────────────────────────────────────────────────────────────

demo-seed: ## Seed demo session data for GIF recording
	bash scripts/demo-session-seed.sh

demo-test: ## Test all demo queries pass before recording
	bash scripts/demo-test.sh

demo-ready: demo-seed demo-test ## Full demo prep in one command

test-export-gates: ## Regression tests for the public-export gates (runner allowlist + personal-token scan)
	bash scripts/test-public-export-gates.sh
