# Lumogis Desktop (Tauri stub)

This directory will hold the Tauri shell that wraps `clients/lumogis-web/dist`
into a desktop app for Linux / macOS / Windows. It is intentionally empty in
Phase 0 — Phase 6 (Desktop & Native) per
`.cursor/plans/cross_device_lumogis_web.plan.md` will:

1. `cargo init` a Tauri 2 project here.
2. Configure it to load the bundled `lumogis-web` build from
   `../lumogis-web/dist` (or to point at a local Caddy URL during dev).
3. Wire in OS-native niceties (system-tray, global hot-key for capture,
   notification fallback when push isn't available on the host OS).
4. Mirror the cookie / session model used by the browser so that the
   desktop shell stores its refresh cookie via the OS keychain.

There is no Rust code yet by design — the Phase 0 pass only reserves
the directory and documents the intended layout.

## Constraints

- The desktop shell must remain a **thin** wrapper: every real product
  decision lives server-side or in `clients/lumogis-web`. The desktop
  app must work against the same `/api/v1/*` contract as the browser.
- It must **not** bypass the Ask/Do permission model or the orchestrator
  rate limiting.
- It must respect `LUMOGIS_PUBLIC_ORIGIN` for the `Origin` header so the
  same CSRF check that protects the browser also protects the desktop
  shell.

See the plan for the full Phase 6 contract.
