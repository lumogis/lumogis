# ADR-114: Overlay GUI E2E test harness (Tauri) — framework choice (LUM-402)

**Status:** Finalised

**Created:** 2026-06-05

**Last updated:** 2026-06-22

**Decided by:** `/explore` (LUM-402) + `/review-plan --arbitrate` R1; framework PoC + harness scaffold landed in `clients/lumogis-search/`

**Issue:** [LUM-402](https://linear.app/lumogis/issue/LUM-402/overlay-gui-e2e-test-harness-tauri-driver-webdriver)

**Related:** [ADR 071](071-lum-397-tauri-overlay-auth-ingest.md) (overlay auth/ingest), LUM-433 (`search-overlay-build.yml` — public release builds), LUM-60 / [ADR 064](064-lum-60-web-e2e-ci.md) (web Playwright optional CI pattern).

## Context

The `lumogis-search` Tauri 2 overlay (`clients/lumogis-search/`, **AGPL-3.0-only, included in public `lumogis/lumogis`** per `AGENTS.md`; the proprietary tree is `clients/lumogis-desktop/`) is verified only by Vitest (happy-dom) and `cargo test`. There is no GUI E2E covering login, search with mid-session 401 refresh, the admin `ingest_paths` settings panel, file upload, and the restart banner.

Two constraints shape the option space:

1. Tauri uses the OS-native webview (**WebKitGTK on Linux**), which has **no Chrome DevTools Protocol**, so plain Playwright cannot drive the real app on Linux.
2. The overlay reaches Core **only via Tauri `invoke` commands** (HTTP lives in Rust; webview CSP is `connect-src 'none'`), so "mock Core" means **mocking the IPC boundary**, not HTTP interception.

The heavy optional CI workflow (`.github/workflows/overlay-e2e.yml`) is **strip-listed** from the public export like the other maintainer-runner workflows; the harness **source** under `clients/lumogis-search/e2e/` exports with the AGPL tree. LUM-433's `search-overlay-build.yml` is a separate **public** release-build workflow — complementary, no file collision.

## Decision

Adopt **WebdriverIO + `tauri-driver`** (the official Tauri WebDriver path), using **`@wdio/tauri-service`** with the `embedded` provider. Use its built-in **`invoke` mocking** for the **default mock-Core leg** (5 MVP scenarios, no Docker) and its **real-binary mode** for the **live-compose integration-smoke leg** — one toolchain satisfies both LUM-402 acceptance legs.

Run on a new **private** path-gated CI workflow (`.github/workflows/overlay-e2e.yml`, gated to `clients/lumogis-search/**`), stripped from the public export. If the framework PoC reveals unacceptable Linux-CI flake, fall back to **`tauri-plugin-playwright`** (Option 2).

**CI placement (locked):** new `overlay-e2e.yml`, strip-listed; the **mock leg is path-gated on PR** (to `dev`/`main`/`master`, no `ci:run-*` label — the mock leg uses no secrets); the **smoke leg is `workflow_dispatch` only**. **macOS is out of scope for v1** — `tauri-driver`/WebKitGTK has no macOS WebDriver.

## Alternatives Considered

- **`tauri-plugin-playwright` / `@srsholmes/tauri-playwright`:** best API consistency with `lumogis-web`'s Playwright and clean browser/tauri-mode mapping, but a 0.2.x, ~3-month-old, low-adoption plugin embedded into the binary — too much maturity risk for the default; kept as the explicit **fallback**.
- **Plain Playwright + WebView2 CDP:** Windows-only (WebKitGTK has no CDP) — cannot be the Linux-CI default.
- **Extended Vitest/JSDOM with mocked `invoke`:** component-level only; does not render the real webview and cannot meet the GUI-E2E or live-stack smoke criteria.

Full comparison: `cursor/explorations/LUM-402-overlay-gui-e2e.md` (lumogis-devtools).

## Consequences

- **Easier:** official, mature (Apache/MIT) Linux-first E2E; mock-Core and live-smoke legs from one harness; reliable for a must-not-flake CI surface.
- **Harder / cost:** introduces a **second e2e idiom** (WDIO) alongside Playwright in `lumogis-web`; Linux CI must install `webkit2gtk-driver` + `xvfb` + the Rust toolchain, and may need WDIO-classic mode and port-4445 hang mitigation.
- **Future chunks must know:** LUM-396's deferred bundled first-run test follow-up and any new overlay UI (LUM-333) should adopt this harness; **macOS desktop E2E is not available** via `tauri-driver`.

## Implementation (as shipped)

- **Harness** (`clients/lumogis-search/`): `wdio.tauri.conf.ts` (`onPrepare` bakes the E2E dist via `npm run build:e2e` and builds the binary with `cargo build --features wdio-e2e`); `e2e/mock-leg.suite.ts` runs all mock scenarios in **one WDIO worker**; `e2e/` holds mock specs (`login`, `search-session` incl. upload + session_expired, `admin-ingest-paths`, `restart-banner`) plus `smoke/live-search.spec.ts`; `e2e/mocks/` and `e2e/helpers/` provide the `invoke` fixtures; `src-tauri` carries the `wdio-e2e` feature + `tauri.wdio-e2e.conf.json` + `wdio-e2e.capability.json`. npm scripts: `e2e`, `e2e:smoke`, `build:e2e`, `e2e:binary-link`. E2E entry uses `rebootForE2e()` after the first boot so Tauri listeners are not re-bound each scenario.
- **CI:** `.github/workflows/overlay-e2e.yml` — `overlay-e2e-mock` (path-gated via `.github/scripts/overlay-e2e-paths.sh`, `xvfb-run -a npm run e2e`) and `overlay-e2e-smoke` (`workflow_dispatch` only, RC compose via `scripts/integration-public-rc.sh`, `npm run e2e:smoke`). Both guarded `if: github.repository != 'lumogis/lumogis'`.
- **Export boundary:** `overlay-e2e.yml` + `overlay-e2e-paths.sh` on `scripts/public-export-strip-list.txt`; harness source still exports.
- **Mock-leg verification (2026-06-22):** **9/9 passing** on Linux with `webkit2gtk-driver` + `xvfb` (Docker `ubuntu:24.04` and CI-equivalent `cargo clean` + full rebuild). Command: `cd clients/lumogis-search && xvfb-run -a npm run e2e`. Smoke leg remains **`workflow_dispatch`** only (not part of mock-leg gate).

## Revisit conditions

- If `tauri-plugin-playwright` reaches ≥1.0 with broad adoption, reconsider for API consistency with `lumogis-web`.
- If macOS desktop E2E becomes a CI requirement (WebKit has no WebDriver — would need a socket-bridge/embedded approach).
- If the PoC shows persistent Linux-CI flake that hardening cannot resolve, switch to Option 2 before expanding the suite.

## Status history

- 2026-06-05: Draft created by `/explore` (LUM-402, headless).
- 2026-06-05: Revised during `/review-plan --arbitrate` R1 — corrected public/private boundary (lumogis-search is AGPL-public); clarified embedded provider; smoke v1 = login+search round-trip (401 refresh deferred).
- 2026-06-22: Finalised — harness scaffold (5 mock specs + smoke) landed; private `overlay-e2e.yml` + path gate + strip-list wired. Green run remains a hardware-gated follow-up (WebKitGTK + xvfb runner).
- 2026-06-22: Mock-leg green on Linux — 9/9 WDIO scenarios (`mock-leg.suite.ts`); harness stability fixes on `dev` (`94bf5a7a7`).
