# Automated testing strategy

Last reviewed: 2026-05-02  
Verified against commit: 98f02b1

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
- **Not in default CI:** Docker integration, Playwright, KG image tests, and parity — not because they are optional forever, but because they need heavier runners; contributors still run them when touching those surfaces, and the Makefile remains the single catalog of commands.

## References

- [CONTRIBUTING.md](../../CONTRIBUTING.md) — setup, venv vs Docker
- [Makefile](../../Makefile) — authoritative target definitions
- [tests/integration/README.md](../../tests/integration/README.md) — live stack integration
- [clients/lumogis-web/README.md](../../clients/lumogis-web/README.md) — codegen, PWA checks, Playwright
