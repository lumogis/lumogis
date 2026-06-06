# ADR-069: Tauri 2 desktop memory search overlay (LUM-329)

> **Superseded (export boundary and product paths)** by **[ADR 081](081-lum-434-export-boundary-reconciliation.md)**. Overlay **behaviour** below remains valid; implement at **`clients/lumogis-search/`** (**Lumogis Search**).

**Status:** Superseded (export boundary) — behaviour record retained
**Created:** 2026-05-27
**Last updated:** 2026-05-27
**Decided by:** `/explore --headless` LUM-329 (Claude Opus 4.7) + `/review-plan --arbitrate` R1
**Finalised by:** `/verify-plan --headless` LUM-329 — implementation confirmed

## Context

The household Docker-track launch needs a **system-wide** memory search surface: global hotkey, small overlay UI, **`GET /api/v1/memory/search`** (limit 5), native open/reveal under operator-controlled **library roots**, and **bearer JWT** in the OS keychain when **`AUTH_ENABLED=true`**. ADR 030 deferred a full Tauri shell until revisit triggers; **LUM-329** implements a **minimal overlay** (not a Phase 6 `lumogis-web` bundle). **Current product:** **Lumogis Search** at **`clients/lumogis-search/`** (**ADR 081**).

## Decision

Ship the overlay as **Tauri 2** **Lumogis Search** (`clients/lumogis-search/`): `tauri-plugin-global-shortcut`, `tauri-plugin-opener`, frameless transparent always-on-top window, hide-on-blur, **`overlay.json`** for non-secret settings, **`keyring`** for access token, explicit **Tauri 2 capability identifiers** (no wildcards), and **explicit CSP** (`connect-src 'none'`) with **HTTP performed from Rust (`reqwest`)** for search so the webview does not need a widening `connect-src` for arbitrary orchestrator origins.

## Alternatives considered

See `.cursor/explorations/LUM-329-tauri-search-overlay.md` (Tauri 1, Electron, Wails, native×3, browser-only) — rejected for maintenance, size, or capability gaps.

## Consequences

**Easier:** Reuses existing orchestrator search contract; orchestrator developers unaffected; Vitest covers URL builder + fetch parsing paths used in dev/tests.

**Harder:** Rust + signing/notarisation path for release-quality binaries; Wayland global shortcuts remain a documented limitation at v0.1.

**Implementation notes (as shipped):**

- **`search_memory`** Tauri command performs **`reqwest`** to `{base}/api/v1/memory/search` with optional bearer from keychain — deviation from the plan’s “fetch in UI TS only” wording, aligned with the plan’s **security** option (Rust HTTP to avoid permissive CSP `connect-src`).
- **`fetchMemorySearch`** in TypeScript delegates to **`invoke("search_memory")`** when running under Tauri; **`fetchMemorySearchWithFetch`** remains for tests and non-Tauri use.
- Path allowlist enforced in Rust with **`is_path_allowed`** + per-request **`canonicalize`**; unit tests include **symlink escape** on Unix.

## Status history

- 2026-05-27: Draft created by `/explore --headless` LUM-329.
- 2026-05-27: Revised during `/review-plan --arbitrate` R1 — locked **`keyring`** for v0.1.
- 2026-05-27: Finalised by `/verify-plan --headless` LUM-329 — implementation confirmed; canonical copy this file.
- 2026-05-27: Filename renumbered **066 → 069** to resolve prefix collision with **`066-lum-124-memory-as-hint.md`** (LUM-124 shipped first).
- 2026-06-05: Path note amended by `/verify-plan` **LUM-435** — **public** AGPL overlay behaviour ships from **`clients/lumogis-search/`** (**ADR 080**); bundled Persona C appliance uses the same overlay UX via shared crate (**ADR 081**).
- 2026-06-05: Export boundary superseded by **ADR 081** (**LUM-434**).

## Relation to other decisions

- **[ADR 030](030-cross-device-client-architecture.md)** — status-history documents partial lift: overlay shipped; Phase 6 full SPA-wrapping shell remains **LUM-44** programme scope.
- **[ADR 081](081-lum-434-export-boundary-reconciliation.md)** — canonical export boundary (**LUM-434**).
- **[ADR 080](080-lum-430-lumogis-search-public-export.md)** — export split as-shipped record (**LUM-432**).
