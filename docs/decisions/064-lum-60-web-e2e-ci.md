# ADR-064: Optional CI for web Playwright (`make web-e2e-prove`) (LUM-60)

**Status:** Finalised
**Created:** 2026-05-23
**Finalised:** 2026-05-23 — `/verify-plan --headless` (implementation confirmed)

## Context

Lumogis ships a Playwright e2e suite (`make web-e2e`, `make web-e2e-prove`) against the same-origin Caddy → Lumogis Web ↔ orchestrator path. Until LUM-60, that suite ran only locally and inside `make verify-public-rc`; no default CI job exercised it on PRs. FP-047 captured the gap.

## Decision

Ship an **optional, path-gated** GitHub Actions workflow **`.github/workflows/web-e2e.yml`** on **`ubuntu-latest`** that:

- Uses the **pinned compose chain** **`-f docker-compose.test.yml -f docker-compose.web-e2e-ci.yml`** with **`--env-file config/test.env.example`** (never relying on `COMPOSE_FILE` inside the env file to pull in `docker-compose.public-rc-stack.yml`).
- Brings up a **slim service allowlist** (postgres, qdrant, stack-control, lumogis-web, orchestrator, caddy) so **Ollama is not started**, with **`depends_on: !override`** on the orchestrator in **`docker-compose.web-e2e-ci.yml`** and **`GRAPH_MODE=disabled`** for CI.
- Overrides **`stack-control.environment.COMPOSE_FILE`** (and orchestrator **`COMPOSE_FILE`**) to the same two-file list the job uses.
- Injects **bootstrap + smoke credentials** from **`LUMOGIS_WEB_SMOKE_EMAIL`** / **`LUMOGIS_WEB_SMOKE_PASSWORD`** repository secrets (same values for bootstrap and Playwright) via compose **`environment`** host interpolation.
- Runs a **host `curl` readiness gate** on **`http://127.0.0.1/`** and **`http://127.0.0.1:8000/healthz`** before Playwright.
- Installs **Node 20**, **`npm ci`**, **`npx playwright install --with-deps chromium`**, then **`make web-e2e-prove`**.
- **Triggers (as shipped 2026-05-23):** `pull_request` to `main`/`master` (types include `labeled` / `unlabeled`), **`workflow_dispatch`**, nightly **`schedule`** with **`actions/checkout`** **`ref: dev`** for scheduled runs.
- **Path gate:** **`.github/scripts/web-e2e-paths.sh`** → `should_run` in `GITHUB_OUTPUT`; PRs without path hits skip with **`SKIP_WEB_E2E_PATHS`**.
- **Label + fork policy:** same-repo PRs require label **`ci:run-web-e2e`** for cred-gated steps; fork PRs log **`SKIP_FORK_PR`** and skip secrets.
- **Failure artefacts:** compose logs + Playwright report dirs uploaded on failure; **`docker compose down -v`** in **`if: always()`** with the same `-f` chain.

**`bash -n`** on **`web-e2e-paths.sh`** runs in **`.github/workflows/ci.yml`** `lint-and-test` and again at the start of **`web-e2e.yml`**.

Current e2e specs **do not** call streaming **`/v1/chat`** completions; **`LUMOGIS_RC_CHAT_STUB`** remains **unread** in orchestrator — any future chat-completion e2e needs mock scaffolding or a dedicated Linear issue before merge.

## Alternatives considered

- Playwright job container + DinD — rejected (exploration Option 2).
- Self-hosted runner for Ollama-backed e2e — deferred / out of scope for this chunk (Option 3).
- Defer CI entirely — rejected as FP-047 closure driver.

## Consequences

- Same-origin web regressions can be caught in CI when maintainers add **`ci:run-web-e2e`** and secrets are configured; optional job does **not** replace **`make verify-public-rc`** / **`verify-public-rc-full`**.
- **`chromium-smoke-shared-user`** must stay at **`workers: 1`** — workflow does not set **`PLAYWRIGHT_WORKERS`**.
- Smoke secrets are sensitive; fork PRs must never receive them (**no `pull_request_target`** pattern that checks out untrusted code with secrets).

## Status history

- 2026-05-23: Draft in `.cursor/adrs/web-e2e-ci.md` from `/explore` + `/review-plan --arbitrate` R1.
- 2026-05-23: Finalised as **ADR-064** — `/verify-plan --headless` LUM-60 (workflow + compose overlay + docs).
- 2026-05-31: Nightly **`schedule`** trigger removed from **`.github/workflows/web-e2e.yml`**; live triggers are **`pull_request`** + **`workflow_dispatch`** only. **`CONTRIBUTING.md`** and **`docs/testing/automated-test-strategy.md`** updated to match.
