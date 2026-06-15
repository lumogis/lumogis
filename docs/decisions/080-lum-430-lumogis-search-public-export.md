# ADR 080: AGPL Lumogis Search public export split (LUM-430)

**Status:** Finalised
**Created:** 2026-06-04
**Last updated:** 2026-06-04
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-04 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/lumogis_search_public_export_retro.md`
**Draft mirror:** `.cursor/adrs/lumogis_search_public_export.md`
**Pre-ship audit:** `.cursor/explorations/LUM-430-desktop-export-boundary-audit.md`

## Context

**LUM-329** / **LUM-398** initially colocated overlay behaviour and bundled sidecars in one interim Tauri tree (**ADR 069**, **ADR 072**). The **Lumogis Search** product name and household overlay behaviour are **AGPL-eligible**; bundled Persona C supervisor and sidecar binaries are not.

Programme **LUM-430** required a **public-shipping** overlay. A read-only boundary audit (`LUM-430-desktop-export-boundary-audit.md`) concluded **NEEDS-SPLIT**: extract client-only surface to **`clients/lumogis-search/`** and scrub public docs so they describe Search on its own terms (**ADR 081**).

## Decision

Ship **Lumogis Search** as a dedicated crate at **`clients/lumogis-search/`** (**AGPL-3.0-only**) included in the **`lumogis/lumogis`** export tree.

### As-implemented surface

| Area | Behaviour |
| --- | --- |
| **Crate** | `lumogis-search` / `lumogis_search_lib`; Tauri 2 + Vite; **no** `bundled` feature or sidecar modules |
| **Build** | `make search-dev`, `make search-build`, `make search-build-client`; `npm run tauri:build` uses `tauri.client-only.conf.json` |
| **Bundled appliance** | Not in the public AGPL tree (maintainer-only; see **ADR 081**) |
| **Export hygiene** | `scripts/public-export-strip-list.txt` excludes maintainer bundled tree and CI; **does not** strip `lumogis-search/` |
| **Public docs** | Public-export templates and operational docs describe **Lumogis Search** only |
| **Identifier** | `com.lumogis.overlay` and keychain service `lumogis-overlay` retained for compatibility |
| **Admin ingest** | Orchestrator HTTP settings/upload/restart flows remain in the public client |

Evidence commits on **`dev`:** `fe6a29a48` (crate), `f2e22f10d` (doc scrub), `8e3668182` (Make/export/debug wiring).

### What was NOT changed (explicit non-goals)

- **ADR 069 / 072 / 076** filenames and primary paths not rewritten in this slice (see **Revisit conditions**).
- ~~**No** shared maintainer dependency from bundled appliance → **`lumogis-search`** yet~~ — **satisfied by LUM-435** (bundled tree path-depends on `lumogis_search_lib`; UI via `@search-ui` factory).
- ~~**No** public **`.github/workflows/search-overlay-build.yml`**~~ — **satisfied (LUM-433 / ADR 082):** public Search build workflow exports; private `hub-build.yml (retired; see deprecated/lumogis-hub-fused/)` remains strip-listed.
- **No** Tauri identifier rename to `com.lumogis.search`.

## Alternatives considered

- **Strip-only / doc-only:** Keep a single proprietary tree and document “use private builds” — rejected; public AGPL tree must ship Search source.
- **Rename in place:** Publish interim single-tree client-only profile as AGPL — rejected; bundled modules would leak without a hard path split.
- **Monorepo workspace member only:** Extract shared crate first, then export — deferred; v1 shipped copy-and-trim for speed.

## Consequences

- **Easier:** Public contributors and `lumogis/lumogis` consumers get a self-contained search overlay; export checks enforce desktop IP boundaries.
- **Harder:** Maintainer bundled tree still carries a redundant client-only CI profile until **LUM-436**; programme **LUM-431** / release docs may still say `lumogis-overlay-*` artefact names.
- **Future work must:** Keep `lumogis-search` off the strip list; keep public docs free of private-tree hints; coordinate bundled track (**LUM-396**) only in the private tree.

## Revisit conditions

- ~~Public CI/release workflow for **`clients/lumogis-search/`**~~ — **done (LUM-433 / ADR 082):** `search-overlay-build.yml` + `search-v*` releases on **`lumogis/lumogis`**.
- ~~Bundled appliance adopts a path dependency on **`lumogis-search`**~~ — **done (LUM-435)**.
- ~~Amend **ADR 069**, **ADR 072**, and **ADR 076** export framing~~ — **done (LUM-434 / ADR 081)**.
- Optional **`com.lumogis.search`** identifier when install coexistence policy is approved.

## Linear linkage (Product OS)

- **Existing issue:** **LUM-430** — https://linear.app/lumogis/issue/LUM-430/programme-v1-lumogis-desktop-for-persona-a-b-and-c (this ADR records the **AGPL export split** slice; programme closure remains operator-driven via **`/linear-update`**)
- **New issue needed:** no for this retrospective record
- **Historical evidence only:** no
- **Follow-ups:** public CI and shared-crate refactor — track under programme children or new Linear issues; do not add markdown-only active queue rows

## Testing retrospective

- **Added/changed:** Vitest suites under `clients/lumogis-search/ui/`; Rust tests in `src-tauri/`.
- **Run:** `npm ci && npm run build && npm test` (25 passed); `cargo build` in `src-tauri/`; `scripts/check-public-export.sh` on maintainer export tree.
- **Gaps:** First public `search-v*` tag smoke on `lumogis/lumogis` (**LUM-437**); no Tauri GUI E2E in AGPL tree (**LUM-402** scope remains private).
- **Docs:** `docs/testing/automated-test-strategy.md` updated in doc-scrub commit.

## Status history

- 2026-06-04: Finalised by `/record-retro` (retrospective as-built record for LUM-430 search export slice).
- 2026-06-05: Revisit conditions **partially closed** by `/verify-plan` **LUM-435** — Hub rename + de-dup onto Search; ADR 069/072/076 path amendments applied.
- 2026-06-05: Export-boundary reconciliation completed by **ADR 081** (**LUM-434**).
- 2026-06-05: Public CI revisit **closed** by `/verify-plan` **LUM-433** — **ADR 082**; first operator `search-v*` tag smoke remains post-publish.

## Relation to other decisions

- **[ADR 081](081-lum-434-export-boundary-reconciliation.md)** — canonical export boundary (**LUM-434**).
- **[ADR 082](082-lum-433-search-overlay-public-ci.md)** — public Search overlay CI (**LUM-433**).
- **[ADR 069](069-lum-329-tauri-search-overlay.md)** — overlay behaviour; public path **`clients/lumogis-search/`**.
- **[ADR 072](072-lum-398-client-only-overlay.md)** — client-only distribution; public artefact **`lumogis-search`**.
- **[ADR 042](042-kg-public-private-export-boundary.md)** — strip-list export mechanism.
- **[ADR 076](076-lum-396-bundled-sidecar-process-manager.md)** — bundled supervisor (maintainer-only; excluded from public export).
