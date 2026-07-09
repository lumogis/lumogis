# ADR-144: Persona A/B Search — Wayland overlay re-summon (LUM-455)

**Status:** Finalised (implementation); **acceptance:** manual Wayland sign-off still open

**Created:** 2026-06-29

**Last updated:** 2026-06-29

**Decided by:** as-shipped implementation after `/verify-plan` on feature branch

**Finalised by:** /record-retro 2026-06-29 (Composer)

**Plan:** `.cursor/plans/archived/LUM-455-wayland-overlay-resummon.plan.md`

**Exploration:** `.cursor/explorations/lum_455_wayland_overlay_resummon_retro.md`

**Draft mirror:** `.cursor/adrs/lum_455_wayland_overlay_resummon.md`

**Linear:** [LUM-455](https://linear.app/lumogis/issue/LUM-455) — **In Review** until exec:human GNOME/KDE Wayland sign-off

**Extends:** [ADR-089](089-lum-446-hub-overlay-handoff.md) (Hub handoff Wayland fallback); supersedes cross-persona hint scope in LUM-456 for Search Wayland recovery

## Context

A/B Search cold-starts hidden and uses in-app global hotkeys to summon the overlay. On Wayland, Tauri `global-shortcut` can silently fail — users who hide the overlay after cold start could be locked out. LUM-455 is the P1 A/B launch blocker addressing guaranteed re-summon on GNOME/KDE Wayland.

## Decision

**Defence-in-depth Wayland recovery for `clients/lumogis-search`:**

1. **CLI `--toggle` / `--show` / `--hide`** via `tauri-plugin-single-instance` (Search binary only) — bind `lumogis-search --toggle` to a DE keyboard shortcut for compositor-owned summon.
2. **Keep in-app global hotkey** (primary on X11; best-effort on Wayland).
3. **Wayland cold-start show-once** in Search `run()` (not Hub `shared_setup`) so first-run/onboarding users see the overlay and setup hint.
4. **`summonHint.ts`** — Wayland-gated recovery hint with DE-specific keybinding guidance and "Don't show on startup" opt-out.
5. **`recovery_confirmed` state** in overlay persistence — flips only on verified visible+focused tray/CLI summon; retires hint safely.
6. **Tray summon** remains additional vector (KDE reliable; GNOME may need AppIndicator extension).

Pure decision logic in **`summon.rs`** (`SummonAction`, `SummonSource`, `DesktopEnv`); glue in `lib.rs`, `commands.rs`, `system_tray.rs`.

**Explicit non-goals:** XDG GlobalShortcuts portal DIY; Hub binary changes; Playwright tray automation (LUM-402).

## Alternatives considered

- **In-app hotkey only** — rejected (silent Wayland failure).
- **Show-once cold-start alone** — insufficient after blur-dismiss.
- **Tray-only fallback** — insufficient on stock GNOME.

## Consequences

**Easier:** Cross-DE Wayland summon without waiting on upstream portal support; 71 Vitest tests green including 13 new summon-hint cases.

**Harder:** Users must configure a DE keybinding on Wayland; manual sign-off blocks Linear **Done**.

**Future chunks must know:**

- **LUM-456** cross-persona hint is superseded for Search Wayland recovery (LUM-455 owns summon path).
- **LUM-431** docs should describe keybinding setup per DE.
- Rust compile + `cargo test` + `make overlay-e2e` are CI gates (no local webkit2gtk on maintainer host).

## Revisit conditions

- Tauri ships XDG GlobalShortcuts portal → revisit zero-config in-app Wayland hotkey.
- GNOME native SNI tray → strengthen tray-as-guaranteed path.
- Manual GNOME + KDE Wayland sign-off passes → **`/linear-update apply-closure LUM-455 --done`**.

## Linear linkage (Product OS)

- **LUM-455** — comment with merge SHA + ADR-144; stay **In Review** until manual Wayland sign-off.
- Recommend comment on **LUM-456** noting supersession by LUM-455 for Search summon scope.

## Testing retrospective

| Item | Detail |
| --- | --- |
| Tests added | `summonHint.test.ts` (13), Rust unit tests in `summon.rs` |
| Commands | `clients/lumogis-search npm test` — **71 passed** |
| CI gates | Rust build, `cargo test`, `make overlay-e2e` — not run locally |
| Gaps | Manual GNOME/KDE Wayland sign-off (exec:human P1); X11 regression manual |
| Follow-ups | LUM-431 docs; LUM-402 overlay e2e; post-v1 portal revisit |

## Status history

- 2026-06-29: Finalised by /record-retro (code on `dev` @ `5ff8f5c19`); manual acceptance gate open.
