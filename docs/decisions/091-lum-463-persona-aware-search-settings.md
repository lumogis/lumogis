# ADR-091: Persona-aware Search/Hub settings UI (LUM-463)

**Status:** Finalised  
**Created:** 2026-06-10  
**Last updated:** 2026-06-10  
**Decided by:** /explore LUM-463; implemented LUM-464; finalised /verify-plan 2026-06-10

## Context

Lumogis Search Settings (`clients/lumogis-search/ui/app.ts`) was built for Persona A/B client-only operators (LUM-397): library roots for local Open/reveal, server ingest paths for remote Core, push upload from laptop to server.

Persona C (Lumogis Hub) reuses the same panel after LUM-435 de-duplication. The Hub wizard already sets the primary **ingest path** from the picked document folder (`bundled_set_library_root`), but Settings contradicted that with “library roots don’t control indexing” and an A/B-oriented three-way paths guide. Copy also said “household memory” while ADR 015 ingests personal-by-default with explicit publish to shared.

Operators reported confusion: Hub runs once on the server; library roots matter for B on laptops, not for pure C on the Hub box.

Exploration: `.cursor/explorations/archived/LUM-463-persona-aware-search-settings.md` (post-verify archive)

## Decision

**Do not fork** the Search overlay app. **Extend `OverlayAppHooks`** with `customizeSettingsPanel(ctx, defaults) → SettingsPanelDescriptor` so Hub can supply persona-C settings markup and copy, with a **shared default** for client-only A/B.

**Bundled (Hub) settings v1:**

- Single operator concept: **“Document folders on this Hub”** (existing ingest paths editor, relabeled).
- **Hide** A/B “How paths work” guide, **library roots** textarea, and **push upload** (v1 — push upload is remote-client only; ingest folder is the upload path on Hub).
- **Hide** orchestrator URL field; Core URL is set at Hub boot via `shared_setup(base_url_override)` → `overlay.json`, not the Settings form.
- **Keep** theme, hotkey, ingest paths editor (admin), restart banner, sign-in when auth on.
- Replace bundled search placeholder “household memory” with **“your memory”**; rely on hit scope pills for shared/personal.
- `overlaySettingsSavePayload` preserves hidden `orchestratorBaseUrl` and `libraryRoots` on save.

**Client-only (A/B) settings:** unchanged layout from LUM-397 (server URL, library roots, admin ingest paths, push upload).

**Implementation:** `clients/lumogis-search/ui/settingsPanel.ts` (module-level compose/save helpers) + `apps/lumogis-server/ui/bundled/hubSettingsMarkup.ts`.

**Documentation:** Persona matrix / Hub README state **C admin on laptop** uses **client-only Search (Persona B layout)** when pointing at Hub URL — not bundled settings (`profile !== "bundled"`).

**Out of scope:** per-folder ingest scope (personal vs shared at watch time); Lumogis Web admin redesign; new backend ingest APIs.

## Alternatives Considered

- **Conditional-only in `app.ts` (Option 1)** — acceptable quick-win; rejected as final architecture because Hub copy should not accumulate in AGPL shared file.
- **Fully forked Settings / second app (Option 3)** — rejected; fights LUM-435 shared factory.
- **Remote feature flags** — rejected; local-first, persona known at profile resolution.
- **Re-enable library_roots ↔ ingest_paths auto-sync (ADR 071 rejected)** — still wrong for B laptops.

## Consequences

**Easier:**

- Persona C operators see one indexing story aligned with the setup wizard.
- A/B remote-client model stays intact without regression (golden HTML + `defaultClientSettingsDescriptor` Vitest).
- Hub-specific UX can evolve in private `apps/lumogis-server/` tree.

**Harder:**

- `OverlayAppHooks` and settings descriptor must stay coordinated when adding settings sections.
- LUM-402 e2e needs a bundled vs client test matrix.
- C admin using Search on a laptop uses B layout — documented in Hub README / reference manual.

**Future chunks must know:**

- `profile === "bundled"` means Hub appliance UI, not “every Persona C human everywhere”.
- Wizard “library folder” === ingest path index 0 on Hub; do not reintroduce conflicting Settings copy.
- Family-visible memory is **publish**, not a second ingest-path list (ADR 015).

## Revisit conditions

- If Lumogis adds **per-folder ingest scope** (personal vs shared at watch time), revisit Hub settings section model — likely new backend + Web admin, not Search-only.
- If **Lumogis Desktop** ships with operator settings parity, reconsider whether Search Settings remains the C operator surface or becomes read-only shortcut to Web.

## Status history

- 2026-06-10: Draft created by /explore LUM-463
- 2026-06-10: Operator locked Q1–Q3 (hide bundled push upload; docs for C-on-laptop; LUM-464 gates LUM-446)
- 2026-06-10: Finalised by /verify-plan LUM-464 — implementation confirmed decision
