# Automated testing strategy

Last reviewed: 2026-05-13  
Verified against commit: ca83054

Lumogis ships a **permanent, layered** automated test setup. Contributors should run the layers that match their change before opening a PR; maintainers rely on the same targets for release confidence.

## Principles

1. **Fast feedback by default** — unit tests and lint run without a live Docker stack (or inside a one-off container with `make compose-test`).
2. **Same patterns in CI** — GitHub Actions (`.github/workflows/ci.yml`) runs Ruff plus orchestrator and stack-control **unit** tests on every PR to `main`/`master`.
3. **Stack-backed tests are explicit** — integration, web, KG, and browser suites need the right Compose services or Node tooling; they are documented here so nothing is “only run before release” without a written home.
4. **Auth defaults for pytest** — `make compose-test` and `make test` force `AUTH_ENABLED=false` so local family-LAN `.env` settings do not break TestClient suites (see `Makefile`).

## Layers (what to run when)

| Layer | Command(s) | Needs |
| --- | --- | --- |
| **Orchestrator unit tests** | `make test` (venv) or `make compose-test` (Docker) | Optional venv with `orchestrator/requirements-dev.txt`, or Docker only |
| **Stack-control unit tests** | Included in `make test`; alone: `make compose-test-stack-control` | Docker |
| **Integration (HTTP against live Core)** | `make test-integration` (venv) or `make compose-test-integration` (Docker; includes FalkorDB overlay) | Full stack up; FalkorDB merge for graph tests |
| **lumogis-graph service tests** | `make compose-test-kg` | Docker (KG test image) |
| **Mock capability contract tests** | `make mock-capability-test` | Python venv with `services/lumogis-mock-capability/requirements-dev.txt` |
| **Web unit / lint** | `make web-test`, `make web-lint` | Node in `clients/lumogis-web` |
| **Web e2e (Playwright)** | `make web-e2e` or `make web-e2e-prove` | Stack + env creds (see `clients/lumogis-web/README.md`) |
| **Caddy security headers** | `make web-caddy-headers` / `make web-caddy-headers-prove` | Caddy + web + orchestrator |
| **Graph inprocess vs service parity** | `make test-graph-parity` | Docker; **destructive** to dev volumes — see `Makefile` |

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
