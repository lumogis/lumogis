# ADR-087: Playwright Ollama pull/delete e2e — manual gate first (LUM-450)

**Status:** Finalised
**Created:** 2026-06-08
**Last updated:** 2026-06-08
**Decided by:** /explore LUM-450; finalised /verify-plan LUM-450

## Context

LUM-423 added Ollama pull/delete to `/admin/system-status`. Vitest (row 2.2.7) proves UI wiring with mocks. LUM-449 shipped async pull (ADR-086) with progress bar and poll endpoints. LUM-450 closes the verify-plan P2 gap: Playwright coverage through **Caddy → Lumogis Web → orchestrator → real Ollama**. ADR-064 (LUM-60) keeps default GitHub Actions web-e2e on a **slim** stack that **excludes Ollama**.

## Decision

Ship **Phase 1** as a **manual-only gated** Playwright spec (`admin_ollama_mutations.spec.ts`) that runs only when **`LUMOGIS_E2E_EXPECT_OLLAMA=1`** (with existing smoke admin creds and **`LUMOGIS_E2E_EXPECT_ADMIN=1`**) against the **full** default Compose stack (includes `ollama`).

- **Hermetic flow:** pull ephemeral **`tinyllama:1.1b`** (override **`LUMOGIS_E2E_OLLAMA_PULL_MODEL`**), wait for LUM-449 async job UI to clear, assert model row, delete same model, assert row gone.
- **Wiring:** spec in **`chromium-smoke-shared-user`** only (`workers: 1`); excluded from default **`chromium`** project.
- **Make target:** **`make web-e2e-ollama-prove`** — sets prove creds + admin + Ollama gates; **not** chained from **`make web-e2e-prove`** or **`verify-public-rc-full`**.
- **Docs:** CONTRIBUTING optional subsection; coverage matrix row **2.2.8**.

**Phase 2 (out of scope):** optional CI workflow + `docker-compose.web-e2e-ollama-ci.yml` with label **`ci:run-web-e2e-ollama`**, and optional **`verify-public-rc-full`** auto-wire for **`make web-e2e-ollama-prove`** (after cold-pull timing baseline) → **LUM-453** (parent **LUM-60**).

## Alternatives considered

- **Slim CI + Ollama** — rejected; violates ADR-064.
- **Pytest-only integration** — rejected; does not test admin SPA or Caddy path.
- **Self-hosted GPU runner** — deferred per ADR-064.
- **RC-only gate** — acceptable supplement; not primary deliverable for Phase 1.
- **`llama3.2:1b` default pull** — rejected (~2× download; mechanics-only test).

Full comparison: `.cursor/explorations/archived/LUM-450-playwright-ollama-mutations-e2e.md`

## Consequences

**Easier:** Maintainers can prove real Ollama pull/delete on the same-origin admin path without destabilizing slim PR web-e2e.

**Harder:** Requires full stack + auth overlay on dev machines; pull tests need **`test.setTimeout(600_000)`** (overrides prove-mode 90s global). Cold **`tinyllama:1.1b`** pulls are multi-minute — timing data informs **LUM-453**.

**Shipped alongside spec (e2e debugging):** null-safe **`embedding_model`** handling in **`AdminSystemStatusView`** — fresh bootstrap discovery can return `embedding_model: null` without SPA crash.

**Preserved:** ADR-064 slim stack; default **`web-e2e.yml`** still asserts Ollama container absent.

## Revisit conditions

- Three consecutive **`workflow_dispatch`** runs on **`ubuntu-latest`** complete tiny-model pull in &lt;15 min **and** cold-pull baseline recorded (cached ~28s observed 2026-06-08) → promote **LUM-453** Phase 2 CI + evaluate **`verify-public-rc-full`** auto-wire.
- Self-hosted CI runners with Ollama model cache → revisit runner strategy.

## Status history

- 2026-06-08: Draft created by `/explore` LUM-450
- 2026-06-08: Finalised by `/verify-plan` LUM-450 — implementation confirmed (`7ac6fc61d` on `dev`)
