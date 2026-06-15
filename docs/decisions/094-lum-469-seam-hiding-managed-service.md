# ADR-094: Persona C seam-hiding — Lumogis Server tray + opt-in login-start

**Status:** Finalised
**Created:** 2026-06-11
**Last updated:** 2026-06-11
**Decided by:** `/explore LUM-469` (composer); finalised by `/verify-plan LUM-469` (Linux P0 shipped)

> Scope is the **Lumogis Hub / Lumogis Server** (private/commercial appliance) delivery seam; stripped from the public AGPL export, like ADR-093. Draft mirror retained at `.cursor/adrs/LUM-469-seam-hiding-managed-service.md`.

## Context

LUM-469 asks for OS-managed background services so Persona C never has to “start the server first.” ADR-093 (LUM-466) already rejected **Option C** — fused seamless delivery with hidden auto-start — in favour of **refined Option B**: a Lumogis Core **server** installer and a separate **client** (browser v1), reusing LUM-396 supervisor logic inside the server product.

The exploration (`.cursor/explorations/LUM-469-seam-hiding-managed-service.md`) evaluated five approaches against the existing `bundled::supervisor` implementation and per-OS platform constraints (notably macOS `SMAppService` mandatory user approval on macOS 13+).

## Decision

**Ship ADR-093-compliant seam-hiding in two phases:**

1. **Server console (Linux P0 — this ADR):** a **Lumogis Server** Tauri profile (`com.lumogis.server`) that supervises Postgres/Qdrant/Core, lives in the **system tray**, opens **Core Web admin** (`/dashboard`) and family web (`/web/`) in the default browser, and hides the setup window after onboarding (no overlay mode).
2. **Opt-in login-start (Linux P0 script; macOS/Windows P1 after LUM-468):** per-OS registration (Linux `systemd --user` unit bound to `graphical-session.target`; macOS SMAppService; Windows logon Scheduled Task) — installer checkbox, default on, revocable in OS settings.

**Do not** implement invisible always-on OS services or fused single-app auto-connect.

### Implementation anchors (as shipped)

| Area | Choice |
| --- | --- |
| Tray seam | Extend AGPL `clients/lumogis-search/src-tauri/src/system_tray.rs` with `TrayMenuSpec::Custom`, `TrayClickBehavior`, and `TrayHostHooks` (`on_open_admin`, `on_open_web`); private strings in `apps/lumogis-server/src-tauri/src/server_tray.rs` only |
| Build | Second binary `lumogis-server`; `HUB_BUILD_PROFILE=server` merges `tauri.bundled.conf.json` + `tauri.server.conf.json` in `build.rs`; release chains `tauri.server.deb-sidecars.json` for separate `Lumogis-Server_*.deb` |
| Loopback | Tray URL builder rejects non-loopback hosts and `::1`; opens `http://127.0.0.1:<port>/dashboard` and `/web/` |
| Login-start | `install/linux/lumogis-server.service` with `@EXECSTART@` templating; `scripts/install-systemd-user-service.sh`; `WantedBy=graphical-session.target` (tray requires graphical session) |
| Client v1 | Browser to loopback Core; Search overlay remains LUM-446 fast-follow |

## Alternatives Considered

- **Manual start only (ADR v1 minimum):** valid deferral; rejected as LUM-469 outcome because seam-hiding value remains in server console + opt-in login-start.
- **Full invisible managed service:** rejected — contradicts ADR-093 Option C decision.
- **Headless daemon first:** deferred to Phase 2 — extract `lumogis-server` CLI after server console split proves stable.

Full matrix: `.cursor/explorations/LUM-469-seam-hiding-managed-service.md`.

## Consequences

**Easier:**

- LUM-469 Linux P0 proceeds without reopening LUM-466/ADR-093.
- Reuses `supervisor.rs`, LUM-467 prove harness patterns, and LUM-457 tray quit semantics.
- Fused Hub (`com.lumogis.hub`) profile unchanged for operators who want in-app overlay.

**Harder / foreclosed:**

- Persona C remains **two products** (server + browser client); no zero-config fused appliance.
- LUM-468 must land before macOS/Windows login-start chunks.
- Windows tray server cannot run as a naive Session 0 service; GUI path uses logon tasks.
- Full `make server-build` deb artefact and graphical-login manual prove recorded as P1 gaps at verify time.

**Future chunks must know:**

- “Seam-hiding” means **one server app to start the stack** + optional **login-start toggle**, not hidden Core.
- Client default URL is installer-written `127.0.0.1` — not mDNS/zero-conf in v1.

## Revisit conditions

- Operator explicitly requests Option C / invisible service → **revise ADR-093** via new exploration, not LUM-469 alone.
- macOS `SMAppService` sandbox rules block server needs → revisit headless daemon + XPC trampoline.
- Two-stream version skew with login-start daemon → tighten min-client-version handshake (ADR-093 deferred item).

## Status history

- 2026-06-11: Draft created by `/explore LUM-469`
- 2026-06-11: Finalised by `/verify-plan LUM-469` — Linux P0 server profile + tray + systemd script shipped; P1 macOS/Windows and full deb/manual prove deferred within ticket
