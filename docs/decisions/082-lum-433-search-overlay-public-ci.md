# ADR 082: Public release CI for Lumogis Search (`search-overlay-build.yml`)

**Status:** Finalised
**Created:** 2026-06-05
**Last updated:** 2026-06-05
**Decided by:** LUM-433 implementation + `/verify-plan`
**Plan:** `.cursor/plans/LUM-433-search-overlay-build-ci.plan.md` (archived after verify)
**Exploration:** `.cursor/explorations/LUM-433-search-overlay-ci.md`
**Draft mirror:** `.cursor/adrs/search_overlay_ci.md`

## Context

**ADR 080** shipped **Lumogis Search** at `clients/lumogis-search/` in the public AGPL export but left **no** public workflow to build installers — only the strip-listed private Hub CI (`hub-build.yml (retired; see deprecated/lumogis-hub-fused/)`). **LUM-433** closes ADR 080 revisit condition #1.

## Decision

Add **`.github/workflows/search-overlay-build.yml`** to the product repo so it **exports** to **`lumogis/lumogis`**:

- **Four-target** Tauri matrix (macOS arm64/x64, Linux x64, Windows x64) aligned with the Hub client-only leg; artefact names **`lumogis-overlay-*`**.
- **SHA-pinned** third-party Actions (precedent: `changelog.yml`, not floating `@v4` from `hub-build.yml (retired; see deprecated/lumogis-hub-fused/)`).
- **Smoke:** `workflow_dispatch` + path-gated `push` to `dev`/`main` → workflow artefacts only.
- **Release:** `search-v*` tags on **`lumogis/lumogis` only** (`github.repository` guard) → `tauri-action` find-or-create GitHub Release with unsigned bundles (signing: **LUM-406**).
- **Export contract:** workflow **not** on `public-export-strip-list.txt`; **`assert_search_overlay_ci_export_contract()`** in `check-public-export.sh` (required presence, strip-list intersection guard, forbidden Hub substring grep).

## Alternatives considered

See `.cursor/explorations/LUM-433-search-overlay-ci.md` — manual `softprops/action-gh-release`, build-only CI, and reusable workflows rejected for v1.

## Consequences

- **Easier:** Public consumers can obtain Search installers after export + `search-v*` tag on `lumogis/lumogis`.
- **Harder:** Dual Tauri CI paths until **LUM-436** removes Hub redundant client-only matrix; artefact naming is a contract for **LUM-406** and release docs.
- **Future work must:** keep workflow off strip list; never reference `apps/lumogis-server/` / `hub-build.yml (retired; see deprecated/lumogis-hub-fused/)` in the YAML (grep guard includes comments).

## Revisit conditions

- **LUM-436** — delete Hub client-only matrix after Search CI proven on public.
- **LUM-406** — signing/notarisation env on release job.
- **`com.lumogis.search`** identifier — optional artefact rename `lumogis-search-*`.
- Org-wide SHA-pin policy — keep header pin table current.

## Status history

- 2026-06-05: Draft created by `/explore --headless` LUM-433.
- 2026-06-05: Finalised by `/verify-plan` — implementation confirmed.

## Relation to other decisions

- **[ADR 080](080-lum-430-lumogis-search-public-export.md)** — public crate split; revisit #1 satisfied.
- **[ADR 037](037-ghcr-publish-public-repo-only.md)** — release from public repo.
- **[ADR 049](049-slsa-artifact-attestations-ghcr.md)** — supply-chain pinning posture.
- **[ADR 061](061-lum-303-public-ci-parity-openapi-check-via-export.md)** — export-contract pattern for required CI paths.
