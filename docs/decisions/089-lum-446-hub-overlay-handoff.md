# ADR-089: Hub wizard-to-overlay handoff (LUM-446)

**Status:** Finalised
**Created:** 2026-06-08
**Last updated:** 2026-06-08
**Decided by:** /explore LUM-446; /create-plan LUM-446; finalised /verify-plan LUM-446

## Context

**Lumogis Hub** (Persona C bundled appliance) shipped with a **windowed setup wizard** and **windowed search UI** after onboarding, while **Personas A/B** use the AGPL **Lumogis Search** overlay (frameless, global hotkey, blur dismiss). Operators completing Hub onboarding saw a persistent desktop window instead of the overlay UX they expect from Search.

**LUM-435** established the shared `lumogis-search` crate seam; Hub path-deps that crate. The exploration compared single-window runtime rechrome vs second window vs process relaunch.

## Decision

**Option 1 — single window, runtime rechrome via Hub-only `enter_overlay_mode`:**

1. **Shared AGPL helpers** in `clients/lumogis-search/src-tauri/src/overlay_window.rs`: `apply_overlay_chrome`, `attach_overlay_dismiss`, `enter_overlay_mode_inner`, `is_wayland_session` (pure-fn unit tests).
2. **Hub-only Tauri command** `enter_overlay_mode` in `apps/lumogis-server/src-tauri/src/lib.rs` — **not** exported on standalone Search (`enter_overlay_mode_not_in_shared_command_names` guard).
3. **Bundled config merge:** `tauri.bundled.conf.json` sets `transparent: true`, `visible: false` at create; wizard branch `show()` + `set_focus()`; onboarded cold-start calls `enter_overlay_mode_inner` with hide-until-hotkey.
4. **Cold-start gate:** branch on `onboarding_complete` **alone** (Thomas R2 — no `library_roots` conjunct).
5. **Wayland:** when `WAYLAND_DISPLAY` is set, cold-start uses `show_focused_once: true` (show-visible fallback) because hide-until-hotkey summonability is unproven on Wayland without manual PoC.
6. **Frontend handoff:** `finishBundledOnboarding` invokes `complete_onboarding` → `refreshSettings` → `enter_overlay_mode` (Vitest contract test).
7. **`hide()` does not stop sidecars** — supervisor teardown remains `RunEvent::Exit` only (LUM-396); manual `.deb` PoC required before public release sign-off.

**CSS:** default **no** edit to shared `clients/lumogis-search/ui/styles.css`; opaque wizard under `transparent: true` relies on `#root` background unless PoC proves Hub-local CSS needed.

## Alternatives Considered

- **Second WebviewWindow** — rejected (complexity, focus/hotkey ownership).
- **Process relaunch after onboarding** — rejected (supervisor risk).
- **Tray-only / hide-on-cold-start on Wayland without fallback** — rejected until summon PoC passes (Thomas R2 escalation).

## Consequences

**Easier:** Persona C matches A/B overlay semantics after onboarding; shared chrome/dismiss logic deduplicated in AGPL crate; Rust + Vitest regression gates in CI.

**Harder:** Linux Wayland best-effort (ADR 069); manual macOS + Linux `.deb` PoC blocks Done on LUM-446 until Thomas sign-off; dynamic overlay resize deferred to follow-up under LUM-430.

## Status history

- 2026-06-08: Draft — /explore LUM-446 Option 1
- 2026-06-08: Plan ready — arbitration R1 + Thomas R2 amendments
- 2026-06-08: Finalised by /verify-plan — implementation confirmed; manual PoC P1 gaps documented
