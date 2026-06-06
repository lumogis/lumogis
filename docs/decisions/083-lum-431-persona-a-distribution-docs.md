# ADR-083: Persona A — Docker-track Lumogis Search distribution docs

**Status:** Finalised
**Created:** 2026-06-06
**Last updated:** 2026-06-06
**Decided by:** /explore --headless LUM-431; implemented and verified LUM-431

## Context

Programme LUM-430 ships Lumogis Search (clients) + Hub (appliance) across Personas A/B/C. **Persona A** (self-hoster) runs Core via **Docker Compose** and installs the **client-only Lumogis Search** overlay pointed at `http://localhost` or an operator-published origin. The runtime architecture is already settled: Persona A uses the **same** public release artefact as Persona B (`lumogis-overlay-*` from `.github/workflows/search-overlay-build.yml`, `search-v*` tags) per **ADR 080 / ADR 082** — only the server URL differs. The gap was purely **operator-facing documentation**: the root README had no Persona A/B/C matrix, the client README did not distinguish Persona A from B, and stale pre-LUM-432 naming (`make desktop-build-client-only`, interim `lumogis-desktop` paths) persisted in operator docs. **LUM-434** closed export-boundary ADR reconciliation but left ADR 081 violations in `docs/LUMOGIS_REFERENCE_MANUAL.md` (Hub maintainer paths in §13 and §15); **LUM-431** absorbed that cleanup while adding the persona matrix.

## Decision

Document Persona A with **layered placement and no new architecture**: the **Persona A/B/C matrix** (reference) lives in `docs/LUMOGIS_REFERENCE_MANUAL.md` (mirrored/linked from `docs/capabilities.md`); the **Persona A install how-to** (install released `lumogis-overlay-*` installer → point at local origin → first-run onboarding) lives in `clients/lumogis-search/README.md`; the **server-URL / localhost-vs-LAN-vs-published-origin** guidance reuses and cross-links the existing `docs/deployment/remote-access.md`; and the root `README.md` carries only a short "how to access Lumogis / Personas" pointer. Persona A is confirmed as the **same** `lumogis-overlay-*` artefact as Persona B (no separate binary). Persona C (**Lumogis Hub**) appears at **product-name** level only in public-shipping docs (ADR 081). Stale `desktop-build-client-only` / interim overlay-path naming is scrubbed in touched docs in favour of `clients/lumogis-search/` + `make search-build`.

## Alternatives Considered

- **Everything in root README** — rejected: bloats the public AGPL landing page and mixes Diátaxis reference (matrix) with how-to (install). See `.cursor/explorations/LUM-431-persona-a-distribution-docs.md`.
- **New dedicated `docs/guides/persona-distribution.md`** — rejected for v1: adds an overlapping doc surface and export-boundary burden disproportionate to the gap; revisit when client count grows (e.g. Lumogis Desktop).

## Consequences

- **Easier:** Operators get a clear Persona A install path reusing existing surfaces; the reference manual is the canonical "home" for the persona matrix that future clients extend; public root README stays lean; LUM-434 residual reference-manual Hub path leaks are scrubbed.
- **Harder:** Cross-links must be kept in sync across four surfaces; Persona C wording must stay ADR 081–compliant in all export-shaped docs.
- **Future work must:** Keep Persona C (Hub) maintainer-tree detail out of AGPL public-export docs; cite (never modify) the `lumogis-overlay-*` / `search-overlay-build.yml` artefact contract from ADR 082; treat the bundle-identifier rename `com.lumogis.overlay` → `com.lumogis.search` as a separate hygiene item.

## Revisit conditions

- When **Lumogis Desktop** (`clients/lumogis-desktop/`) ships, re-evaluate whether the persona matrix should graduate from the reference manual into a dedicated `docs/guides/persona-distribution.md` (Option 3).
- If the `lumogis-overlay-*` artefact name changes (e.g. rename to `lumogis-search-*`), update all Persona A doc references in lockstep.
- If Persona A ever requires a distinct binary or config profile (not the case today), this docs-only decision must be reopened as an architecture decision.

## Status history

- 2026-06-06: Draft created by /explore --headless LUM-431
- 2026-06-06: Finalised by /verify-plan — implementation confirmed decision (LUM-431)
