# Automated testing strategy

Last reviewed: 2026-05-13  
Verified against commit: ca83054

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
| **Web e2e (Playwright)** | `make web-e2e` or `make web-e2e-prove` | Stack + env creds (see `clients/lumogis-web/README.md`) | **Real browser**: gate UI flows, workflows, mobile viewports; **`verify-public-rc`** uses a **narrow** Playwright gate; **`verify-public-rc-full`** adds **full** signed-in navigation after seeding a smoke user. |
| **Caddy security headers** | `make web-caddy-headers` / `make web-caddy-headers-prove` | Caddy + web + orchestrator | Same-origin / security header contracts at the edge. |
| **Graph inprocess vs service parity** | `make test-graph-parity` | Docker; **destructive** to dev volumes — see `Makefile` | Core behaviour against **in-process** graph plugin vs **lumogis-graph** HTTP path — optional tail of **`verify-public-rc-full`**. |
| **DB migrations gate** | `scripts/check-migrations-fresh-db.sh` (via **`verify-public-rc-full`** on `main`) | Docker / tooling per script | Fresh DB bootstrap + migration continuity — part of the **full** RC gate on `main`. |
| **Export shape** | `scripts/create-upstream-export-tree.sh` + `scripts/check-public-export.sh` on the export dir | None beyond bash/git | Ensures the tree that could ship to **`lumogis/lumogis`** has correct licence posture and **no** forbidden paths — chained inside **`verify-public-rc`** / publish workflow. |

## CI vs local

- **CI today:** `ruff check` / `ruff format --check` on `orchestrator/`, `pytest` on `orchestrator/tests/`, `pytest` on `stack-control/test_main.py`.
- **Not in default CI:** Docker integration, Playwright, KG image tests, and parity — not because they are optional forever, but because they need heavier runners; contributors on **`dev`** still run the **relevant** subset when touching those surfaces. **Maintainers** run the **full** `make verify-public-rc-full` on the **release line** before treating `main` as publish-ready.

## Release gates (LUM-225)

These Makefile targets are for **maintainers on `main`** (or a `promote/clean-main` branch) before running `/publish-private-main-to-public`. They require Docker, `.env` bootstrapped from `.env.example`, and for the full gate: Playwright smoke credentials.

| Layer | Command | Prerequisites |
| --- | --- | --- |
| **RC smoke gate** | `make verify-public-rc` | Docker; `.env` from `.env.example`; no running Core required for unit layers |
| **RC full gate** | `make verify-public-rc-full` | As above + `LUMOGIS_WEB_SMOKE_EMAIL` / `LUMOGIS_WEB_SMOKE_PASSWORD` for Playwright |

`verify-public-rc` chains (in order): `scripts/check-main-hygiene.sh` → `compose-policy-check` → `compose-test` → `web-codegen-check` (skippable via `VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK=1`) → `web-lint` → `web-test` → `web-build` → `scripts/integration-public-rc.sh full-cycle` → `scripts/create-upstream-export-tree.sh` → `scripts/check-public-export.sh`.

`verify-public-rc-full` runs the full smoke chain first, then adds `web-e2e-prove` (skippable via `VERIFY_PUBLIC_RC_SKIP_WEB_E2E=1`) and optional `test-graph-parity` (opt-in via `LUMOGIS_RC_GRAPH_PARITY=1`).

Integration tests run via `scripts/integration-public-rc.sh full-cycle` against the triple-merged Compose stack (`docker-compose.yml` + `docker-compose.test.yml` + `docker-compose.public-rc-stack.yml`) using the `integration and public_rc` pytest marker predicate.

## References

- [CONTRIBUTING.md](../../CONTRIBUTING.md) — setup, venv vs Docker
- [Makefile](../../Makefile) — authoritative target definitions
- [tests/integration/README.md](../../tests/integration/README.md) — live stack integration
- [clients/lumogis-web/README.md](../../clients/lumogis-web/README.md) — codegen, PWA checks, Playwright
