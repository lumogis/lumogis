# Automated testing strategy

Last reviewed: 2026-07-10
Verified against commit: 01a7d31

Lumogis ships a **permanent, layered** automated test setup. **Where** you run the **full** stack matters: see **Dev vs `main` (comprehensive testing)** below.

## Dev vs `main` (comprehensive testing)

| Line | Testing expectation |
| --- | --- |
| **`origin/dev`** | **Targeted** layers only: run the commands that exercise **your** change (e.g. orchestrator unit tests for a Core fix; `make web-test` for a web-only change). Fast feedback wins. You **do not** treat `dev` as the place to prove the entire product slice before every push. |
| **`origin/main`** (and branches about to fast-forward `main`, e.g. `promote/clean-main` when it carries the same `Makefile` / scripts as `main`) | **Comprehensive** testing: the **full release gate** — on a **clean checkout of the release line**, run **`make verify-public-rc-full`** when that target exists in `Makefile`. That umbrella chains hygiene, multi-tree unit coverage, web install/lint/test/build, the **public RC Compose stack** (`docker-compose.yml` + `docker-compose.test.yml` + `docker-compose.public-rc-stack.yml` via `scripts/integration-public-rc.sh`), Playwright gate + full signed-in flows, export-tree checks, migrations, and optional heavier Docker targets. **Publishing** (`docs/release/public-agpl-release-workflow.md`) assumes this bar has been met on the **private `main` SHA** you export — not on a stray `dev` working copy whose `Makefile` may omit RC targets. |

**Why:** `dev` and `main` **diverge** on purpose. The extended RC `Makefile` targets and `scripts/integration-public-rc.sh` are maintained on the **release candidate** line; running the **full** matrix only there avoids false “target missing” errors and keeps signal aligned with what you ship and publish.

## Principles

1. **Fast feedback by default** — on `dev`, unit tests and lint run without a live Docker stack (or inside a one-off container with `make compose-test`).
2. **Same patterns in CI** — GitHub Actions (`.github/workflows/ci.yml`) runs Ruff plus orchestrator and stack-control **unit** tests on every PR to `main`/`master`.
3. **Stack-backed tests are explicit** — integration, web, KG, and browser suites need the right Compose services or Node tooling; they are documented here so nothing is “only run before release” without a written home.
4. **Auth defaults for pytest** — `make compose-test` and `make test` force `AUTH_ENABLED=false` so local family-LAN `.env` settings do not break TestClient suites (see `Makefile`).

## Coverage matrices (feature → test)

**Companion to this doc:** [docs/testing/README.md](README.md) indexes **TEST-COVERAGE-MATRIX-*** files for core, web, and related surfaces. They answer *which product behaviours have test evidence*, while `scripts/debug/inventory.tsv` (LUM-377) answers *which commands to run*.

- **Statuses** (✅/🟡/❌/🚫) come from code/plan audit, not from capabilities prose alone.
- **v1 baseline:** LUM-384 seed; **ongoing:** rows updated when **`/verify-plan`** closes a feature plan. **Structure gate (LUM-429):** `make coverage-matrix-check` in CI.

## Layers — commands, needs, and what they cover

| Layer | Command(s) | Needs | What it covers |
| --- | --- | --- | --- |
| **Orchestrator unit tests** | `make test` (venv) or `make compose-test` (Docker) | Optional venv with `orchestrator/requirements-dev.txt`, or Docker only | Core FastAPI routes, services, plugins, auth/session logic, MCP tools, most business rules using **TestClient** and mocks — **no** requirement for Postgres/Qdrant/Ollama to be up on the host. |
| **Stack-control unit tests** | Included in `make test`; alone: `make compose-test-stack-control` | Docker | `stack-control` process supervision helpers and small Python surface. |
| **Integration (HTTP against live Core)** | `make test-integration` (venv) or `make compose-test-integration` (Docker; includes FalkorDB overlay) | Full stack up; FalkorDB merge for graph tests | **Real** HTTP to Core against a **live** stack: ingest/search, entities, sessions, connectors, graph APIs when FalkorDB is in the compose file — validates wiring and persistence paths unit tests skip. |
| **RC integration subset (`public_rc`)** | Invoked from **`make verify-public-rc`** on `main` via `scripts/integration-public-rc.sh` | RC compose triple (`docker-compose.yml` + `docker-compose.test.yml` + `docker-compose.public-rc-stack.yml`), `config/test.env.example` | Deterministic integration slice (graph service mode, mock capability, etc.) used in **release gates** — see `Makefile` on **`main`**. |
| **lumogis-graph service tests** | `make compose-test-kg` | Docker (KG test image) | KG service unit tests in an isolated image — webhook parity, scheduler off, no accidental hits to your dev volumes. |
| **Mock capability contract tests** | `make mock-capability-test` | Python venv with `services/lumogis-mock-capability/requirements-dev.txt` | Second-party capability HTTP contract and manifest behaviour. |
| **Web unit / lint** | `make web-test`, `make web-lint` | Node in `clients/lumogis-web` | Client TypeScript/Vitest, ESLint, OpenAPI codegen checks — **no** browser launch. |
| **Search overlay unit** | `cd clients/lumogis-search && npm test` | Node 20+ in `clients/lumogis-search` | Vitest for overlay UI (`searchClient`, `overlayUi`, onboarding DOM helpers) — **no** full Tauri shell required. |
| **Search overlay Rust** | `cd clients/lumogis-search/src-tauri && cargo test` | Rust toolchain | Tauri crate unit tests (auth, path allowlist, overlay config) — invoked via `scripts/debug/rust.sh` or directly. |
| **Web e2e (Playwright)** | `make web-e2e` or `make web-e2e-prove` | Stack + env creds (see `clients/lumogis-web/README.md`) | **Real browser**: gate UI flows, workflows, mobile viewports; **`verify-public-rc`** uses a **narrow** Playwright gate; **`verify-public-rc-full`** adds **full** signed-in navigation after seeding a smoke user. |
| **Overlay GUI e2e (WDIO + tauri-driver)** | `make overlay-e2e` (mock) / `make overlay-e2e-smoke` (live) | **Linux + xvfb**; `webkit2gtk-driver` + Rust + Node 20 (LUM-402, [ADR 114](../decisions/114-lum-402-overlay-gui-e2e.md)) | **Real WebKitGTK webview** for the Lumogis Search Tauri overlay (login, search, admin `ingest_paths`, upload, restart banner). Mock leg mocks the Tauri `invoke` IPC (no Docker); smoke leg does one live login+search round-trip against the RC Core. WDIO is a **second e2e idiom** alongside web Playwright; **macOS has no WebDriver** (Linux-only v1). |
| **Caddy security headers** | `make web-caddy-headers` / `make web-caddy-headers-prove` | Caddy + web + orchestrator | Same-origin / security header contracts at the edge. |
| **Graph inprocess vs service parity** | `make test-graph-parity` | Docker; **destructive** to dev volumes — see `Makefile` | Core behaviour against **in-process** graph plugin vs **lumogis-graph** HTTP path — optional tail of **`verify-public-rc-full`**. |
| **DB migrations gate** | `scripts/check-migrations-fresh-db.sh` (via **`verify-public-rc-full`** on `main`) | Docker / tooling per script | Fresh DB bootstrap + migration continuity — part of the **full** RC gate on `main`. |
| **Export shape** | `scripts/create-upstream-export-tree.sh` + `scripts/check-public-export.sh` on the export dir | None beyond bash/git | Ensures the tree that could ship to **`lumogis/lumogis`** has correct licence posture and **no** forbidden paths — chained inside **`verify-public-rc`** / publish workflow. |

## CI vs local

- **CI today:** `ruff check` / `ruff format --check` on `orchestrator/`, `pytest` on `orchestrator/tests/`, `pytest` on `stack-control/test_main.py`.
- **Optional path/label-gated Playwright:** **`.github/workflows/web-e2e.yml`** (LUM-60) runs **`make web-e2e-prove`** against a slim Compose stack when a maintainer adds **`ci:run-web-e2e`** on same-repo PRs that touch the gated paths, or on **`workflow_dispatch`** (see [CONTRIBUTING.md](../../CONTRIBUTING.md) § *Optional CI — web Playwright*). It does **not** replace **`make verify-public-rc`** / **`verify-public-rc-full`**.
- **Private overlay GUI e2e:** **`.github/workflows/overlay-e2e.yml`** (LUM-402, [ADR 114](../decisions/114-lum-402-overlay-gui-e2e.md)) runs the WDIO + `tauri-driver` mock leg on PRs that touch **`clients/lumogis-search/**`** (path-gated, no label — mock uses no secrets), and a live-compose smoke leg on **`workflow_dispatch`** only. The workflow + `.github/scripts/overlay-e2e-paths.sh` are **strip-listed** from the public export (heavy maintainer runner; smoke uses secrets); the harness **source** under `clients/lumogis-search/e2e/**` exports with the AGPL tree. Distinct from LUM-433's public `search-overlay-build.yml` (release builds).
- **Not in default CI:** Docker integration, Playwright (except the optional LUM-60 workflow above), KG image tests, and parity — not because they are optional forever, but because they need heavier runners; contributors on **`dev`** still run the **relevant** subset when touching those surfaces. **Maintainers** run the **full** `make verify-public-rc-full` on the **release line** before treating `main` as publish-ready.

## Release gates (LUM-225)

These Makefile targets are for **maintainers on `main`** (or a `promote/clean-main` branch) before running `/publish-private-main-to-public`. They require Docker, `.env` bootstrapped from `.env.example`, and for the full gate: Playwright smoke credentials.

| Layer | Command | Prerequisites |
| --- | --- | --- |
| **RC smoke gate** | `make verify-public-rc` | Docker; `.env` from `.env.example`; no running Core required for unit layers (including **`web-codegen-check`** / **`openapi-check`**, which use offline `dump_openapi` — not a live stack) |
| **RC full gate** | `make verify-public-rc-full` | As above + `LUMOGIS_WEB_SMOKE_EMAIL` / `LUMOGIS_WEB_SMOKE_PASSWORD` for Playwright |

`verify-public-rc` chains (in order): `scripts/check-main-hygiene.sh` → `compose-policy-check` → `compose-test` → `web-codegen-check` (offline `dump_openapi` vs committed snapshot — **no** live orchestrator; skippable via `VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK=1`) → `web-lint` → `web-test` → `web-build` → `openapi-breaking-check` (LUM-313; offline oasdiff gate on the committed snapshot, base `HEAD~1`) → `scripts/integration-public-rc.sh full-cycle` → `scripts/create-upstream-export-tree.sh` → `scripts/check-public-export.sh`.

The `openapi-breaking-check` step (ADR-061 deferred this "runnable proof" of the OpenAPI gate from LUM-303 to LUM-313) needs the `oasdiff` Go dev tool. Because **CI** (`.github/workflows/ci.yml` `openapi-check` job) is the **binding** gate, a missing `oasdiff` degrades to a documented `WARN` rather than failing the local smoke gate. Set `VERIFY_PUBLIC_RC_REQUIRE_OPENAPI_BREAKING=1` to make it a hard local gate (recommended on the release line, where `oasdiff` should be installed: `go install github.com/oasdiff/oasdiff@v1.15.2`).

`verify-public-rc-full` runs the full smoke chain first, then adds `web-e2e-prove` (skippable via `VERIFY_PUBLIC_RC_SKIP_WEB_E2E=1`) and optional `test-graph-parity` (opt-in via `LUMOGIS_RC_GRAPH_PARITY=1`).

Integration tests run via `scripts/integration-public-rc.sh full-cycle` against the triple-merged Compose stack (`docker-compose.yml` + `docker-compose.test.yml` + `docker-compose.public-rc-stack.yml`) using the `integration and public_rc` pytest marker predicate.

## Manual release verification (LUM-385)

Automated RC gates do not replace **operator manual checks** on a real host (fresh GHCR boot, compose health, LLM routing, persistence). After `make verify-public-rc-full` on a release candidate SHA, maintainers record sign-off using [docs/RELEASE-MANUAL-CHECKLIST.md](../RELEASE-MANUAL-CHECKLIST.md) (`MS-001` … `MS-010`). Coverage matrices cite those IDs on 🚫 rows. Rule: no automation **and** no checklist row ⇒ treat as untested for release.

## Test inventory (canonical)

Machine-readable source: **`scripts/debug/inventory.tsv`**. Human view: **`make test-list`**
(same columns).

| id | Command | Wrapper | Stage | Heavy | Private tree only |
| --- | --- | --- | --- | --- | --- |
| orchestrator-unit | `make test` | unit | local-dev | no | no |
| stack-control-unit | (in `make test`) | unit | local-dev | no | no |
| compose-unit | `make compose-test` | unit | local-dev | no | no |
| test-kg | `make test-kg` | none | local-dev | yes | yes |
| mock-capability | `make mock-capability-test` | none | local-dev | no | yes |
| test-integration | `make test-integration` | integration | local-dev | yes | no |
| rc-integration | `scripts/integration-public-rc.sh full-cycle` | integration | release-rc | yes | no |
| e2e-ingest-restart | `make e2e-ingest-restart` | integration | opt-in-heavy | yes | no |
| graph-parity | `make test-graph-parity` | integration | opt-in-heavy | yes | no |
| web-unit | `make web-test` | web | local-dev | no | no |
| search-vitest | `cd clients/lumogis-search && npm test` | none | local-dev | no | no |
| search-rust | `cargo test` (lumogis-search) | rust | local-dev | no | no |
| web-e2e | `make web-e2e` | web | opt-in-heavy | yes | no |
| lint-python | `make lint` | lint | local-dev | no | no |
| openapi-codegen / breaking | `make web-codegen-check`, `openapi-breaking-check` | none | ci-main, release-rc | no | no |
| compose-policy | `make compose-policy-check` | none | ci-main | no | no |
| export-hygiene | export tree + `check-public-export.sh` | none | release-rc | no | no |
| verify-public-rc | `make verify-public-rc` | none | release-rc | yes | no |
| verify-public-rc-full | `make verify-public-rc-full` | none | release-full | yes | no |

See **`scripts/debug/inventory.tsv`** for the full ~26-row inventory (prereqs column).

## Release-stage gating map

| Stage | What runs | Automation | Gap / notes |
| --- | --- | --- | --- |
| **Local `dev` (per change)** | Targeted subset (`make test`, `make web-test`, …) | Manual / agent choice | **`make debug`** + **`make test-list`** (LUM-377) — no path→suite helper yet |
| **CI on PR/push to `main`/`master`** | ci.yml unit + lint + compose-policy; optional web-e2e label | GitHub Actions | Default PR gate is Core + stack-control unit tests; search overlay: `clients/lumogis-search` Vitest + `cargo test` locally or in extended gates |
| **`dev` → private `main`** | hygiene, `make test`, export checks, `verify-public-rc(-full)` for release-scale | `prepare-private-release-from-dev` skill | Judgement manual; use **`verify-public-rc-full`** on release line |
| **private `main` → public `main`** | `verify-public-rc-full` on exact SHA, then export | `publish-private-main-to-public` skill | Run Make targets directly; debug wrappers optional for logs |

## Debug runners (`scripts/debug/`)

- **`make debug`** — fast/safe chain (unit, lint, web unit, rust); summary on stdout, full log under **`target/debug-logs/`**.
- **`make test-list`** — print inventory table.
- **Heavy suites** — `graph-parity`, `restart-e2e`, RC integration, web e2e: require **`--heavy`** or **`LUMOGIS_DEBUG_HEAVY=1`**.
- **Release umbrellas** — still **`make verify-public-rc`** / **`make verify-public-rc-full`** (not replaced by wrappers).
- **Python summaries** — dev-only **`pytest-agent-digest`** via **`PYTEST_ADDOPTS`** in `unit.sh` only (CI `make test` unchanged).

Operator doc: [scripts/debug/README.md](../../scripts/debug/README.md).

## References

- [docs/testing/README.md](README.md) — coverage matrices (feature → test evidence)
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — setup, venv vs Docker
- [Makefile](../../Makefile) — authoritative target definitions
- [tests/integration/README.md](../../tests/integration/README.md) — live stack integration
- [clients/lumogis-web/README.md](../../clients/lumogis-web/README.md) — codegen, PWA checks, Playwright
