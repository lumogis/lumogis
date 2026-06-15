# ADR-090: System tray — all personas A/B/C (LUM-457)

**Status:** Finalised
**Created:** 2026-06-09
**Last updated:** 2026-06-09
**Decided by:** /explore LUM-457; Thomas scope lock 2026-06-09; finalised by /verify-plan LUM-457

## Context

Lumogis desktop clients (Personas A/B/C) use a frameless overlay that hides on blur and may stay hidden until summoned (**ADR-069**, **ADR-089** / LUM-446). Operators need a discoverable recovery path besides the global hotkey — especially on Linux Wayland where hotkey summon is unproven (**LUM-455**) and **mid-wizard blur-dismiss** can strand first-run users. Thomas committed **system tray to v1.0** (LUM-457, child of LUM-430).

Hub **hide ≠ quit**: sidecars keep running until real app exit (**LUM-396**). Tray **Quit** on Hub must trigger supervisor teardown; on Search, client exit only.

## Decision

**Shared Rust `system_tray` module in `lumogis_search_lib`:**

1. Enable Tauri **`tray-icon`** + **`image-png`** features on Search and Hub direct `tauri` deps.
2. Install tray from Rust in the **`shared_setup` tail** (from first paint, including wizard).
3. **`toggle_overlay_window(app)`** shared by hotkey, tray left-click, and menu **Show Lumogis**.
4. **Always attach a menu** with constant **`Show Lumogis`** + **Quit**; left-click toggles (`show_menu_on_left_click(false)`).
5. **`TrayConfig`** (two fields): `quit_mode` + `close_requested`; Hub registers **`TrayHostHooks { on_supervisor_quit: request_app_quit }`** before `shared_setup`.
6. Hub teardown: **`request_app_quit`** (tray Quit + signals) vs **`perform_bundled_shutdown`** only on **`RunEvent::Exit`**.
7. Icon: `default_window_icon()` then **`include_bytes!("../icons/32x32.png")`** fallback.
8. **Capabilities:** Rust-built tray uses the **`tray-icon` cargo feature**; **`core:tray` / `core:menu` ACL entries omitted** unless Phase 0 matrix row A fails on Wayland (least privilege).

Tray **adds** summon path; does **not** replace global hotkey.

## Alternatives Considered

See `.cursor/explorations/LUM-457-system-tray-all-personas.md` — JS-primary tray, Hub-only tray, menu-only, tray gated on onboarding, native per-OS tray, defer to KSNI/GTK4 upstream (all rejected).

## Consequences

**Easier:** Consistent summon UX; wizard-phase recovery; AGPL/private boundary via `TrayHostHooks`.

**Harder:** GNOME/Wayland tray visibility best-effort; manual matrix required for Done; Playwright tray deferred (**LUM-402**).

## Revisit conditions

- Tauri GTK4/WebKit6 tray behaviour change.
- GNOME native StatusNotifier without extensions.
- Phase 0 Wayland row A failure → add capability ACL entries.

## Status history

- 2026-06-09: Draft created by /explore LUM-457
- 2026-06-09: Locked decisions — Thomas
- 2026-06-09: Finalised by /verify-plan LUM-457 — implementation confirmed
