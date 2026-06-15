# ADR-100: Fused Hub cleanup — Lumogis Server rename and quarantine (LUM-491)

**Status:** Finalised
**Created:** 2026-06-14
**Last updated:** 2026-06-15
**Decided by:** `/explore LUM-491`; implemented and verified `/verify-plan LUM-491`

## Context

ADR-093/094/095 retired the fused single-app Hub (`com.lumogis.hub`, `Lumogis_*.deb`) as the Persona C product in favour of **Lumogis Server** (native Core install) + thin clients. The dead fused code still lived in `apps/lumogis-hub/`, which caused confusion and export-boundary risk during rename. LUM-491 quarantines fused-only artefacts, renames the shared crate to `apps/lumogis-server/`, and hardens the public AGPL export boundary.

The crate was **shared**: `lumogis_hub_lib` hosted both fused `run()` and live `run_server()`, plus the LUM-396 bundled supervisor (`bundled/**`) and setup wizard UI used by the Server profile. Shared symbols (`enter_overlay_mode`, tray overlay helpers in `lumogis-search`) remain live.

## Decision

**Option A (implemented):** Rename `apps/lumogis-hub/` → `apps/lumogis-server/` (`lumogis-server` / `lumogis_server_lib` / bin `lumogis-server`); delete fused-only entangled code in place (git-recoverable at `LAST_GOOD_FUSED_BUILD_SHA`); quarantine standalone fused files under `deprecated/lumogis-hub-fused/` (strip-listed, not built); repoint `Makefile.hub.mk` → `Makefile.server.mk` with full `hub-*` → `server-*` target rename.

**Export boundary (same squash-merge commit as rename):** update `scripts/public-export-strip-list.txt`; add `assert_export_has_no_apps_subtree` and `scripts/check-rename-export-atomic.sh`; extend export pytest and LUM-433 forbidden-substring guards for `apps/lumogis-server`.

**Review correction (shipped with LUM-491):** live `.github/workflows/hub-build.yml` deleted (snapshot under `deprecated/lumogis-hub-fused/`); dormant workflow provided no signal after path rename. Remaining private Server CI tracked as **LUM-492**.

**Identity:** `com.lumogis.server` is the sole appliance identifier. There is **no released fused-Hub installed base** — the `com.lumogis.hub` → `com.lumogis.server` change has **no data-migration impact**.

## Alternatives considered

- **Option B — extract new crate:** rejected (re-coupling risk; more moving parts).
- **Option C — strip in place, keep `lumogis-hub` name:** rejected as primary; retained only as timing fallback in plan.

## Consequences

**Easier:** one crate named for the Server product; fused build target removed; export leak guarded by strip-list + `apps/` subtree assertion + atomic rename script.

**Harder / foreclosed:** fused single-app appliance no longer buildable from live tree (recover via git SHA or `deprecated/`).

**Future work must know:**

- Any future private Server CI workflow must stay strip-listed (**LUM-492**).
- `clients/lumogis-search/src-tauri/build.rs` keeps `com.lumogis.server` guard only (`com.lumogis.hub` arm removed).
- Shared supervisor/UI remain at `apps/lumogis-server/src-tauri/src/bundled/` and `apps/lumogis-server/ui/bundled/`.

## Status history

- 2026-06-14: Draft created by `/explore LUM-491` (`.cursor/adrs/lum-491-fused-hub-cleanup.md`).
- 2026-06-15: Finalised by `/verify-plan LUM-491` — implementation confirmed; merged to `dev` at `39ebcb24b`.
