# ADR 032: AGPL-3.0-only repository licence metadata
**Status:** Finalised
**Created:** 2026-05-01
**Last updated:** 2026-05-01
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-01 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** *(maintainer-local only; not part of the tracked repository)*
**Draft mirror:** *(maintainer-local only; not part of the tracked repository)*

## Context

Lumogis first-party source used mixed licence metadata: many files declared AGPL using an **SPDX `…-or-later`** identifier, and human-facing docs used shorthand such as “AGPL-3.0” without pinning “only” vs “or later”. The project chose to standardise on **AGPL-3.0-only** for clarity and consistency with SPDX. This ADR records that **as-shipped** state after a cross-repo metadata pass (commit `78b3772`), without implying there was a prior formal plan file for the sweep.

## Decision

Lumogis **repository licence metadata** is **AGPL-3.0-only** (GNU Affero General Public License **v3.0 only**, SPDX **`AGPL-3.0-only`**).

- **SPDX headers** on Lumogis-owned source, SQL migrations, Dockerfiles, Caddyfile, and the web client tree that already carried SPDX licence lines now read **`SPDX-License-Identifier: AGPL-3.0-only`** (comment style preserved per language: `#`, `//`, `/* */`, `--`).
- **`clients/lumogis-web/package.json`** and the workspace-root **`clients/lumogis-web/package-lock.json`** package `license` field: **`AGPL-3.0-only`**.
- **`LICENSE`:** A short **project notice** (copyright + SPDX + pointer) was added **before** the official GNU AGPLv3 text. The **FSF licence body** (from “GNU AFFERO GENERAL PUBLIC LICENSE” through the end of the standard terms) is **unchanged**.
- **`README.md`** states the v3.0-only posture and default SPDX for sources unless otherwise stated.
- **`CONTRIBUTING.md`** states contributions are under the project licence **AGPL-3.0-only** and summarises maintainer sublicensing/relicensing rights in line with the CLA intent.

### What was NOT changed

- **No functional/runtime code** changes were in scope — identifier and documentation strings only.
- **No edits** to third-party licence blocks, vendored trees, or unrelated “or later” English (e.g. “Phase 1 or later”) outside licence metadata.
- **Skill-owned** local plan content under ***(maintainer-local only; not part of the tracked repository)*** (gitignored in release-candidate trees) and existing **`docs/decisions/`** files were not bulk-rewritten in this pass; residual wording may be updated in later skill-driven doc passes (see Revisit conditions).

## Alternatives considered

- **Not chosen at ship time: the SPDX `…-or-later` form** — would preserve compatibility with future AGPLv4+ if the FSF ever publishes one under the same upgrade path; rejected for this product decision in favour of explicit **v3.0 only**.
- **Not chosen: leave ambiguous `AGPL-3.0` without SPDX `-only` / `-or-later` suffix** — rejected because SPDX expects a precise licence list identifier for machine readability.

## Consequences

- SPDX-aware tooling and npm metadata align on **`AGPL-3.0-only`**.
- Contributors reading `README.md` / `CONTRIBUTING.md` see consistent “v3.0 only” language.
- **Residual drift risk:** local plan excerpts under ***(maintainer-local only; not part of the tracked repository)*** may still show old example SPDX values until updated by plan skills; some finalised ADRs may still use shorthand “AGPL-3.0” in narrative bullets — cite **ADR 032** for the canonical SPDX expression when updating those documents through the normal skill workflow.

## Revisit conditions

- If the project **re-licences** under a different SPDX expression, supersede this ADR with a new numbered decision and update headers/metadata in one coordinated pass.
- If **legal review** requires preamble wording changes, amend only the project notice in `LICENSE` / docs — not the FSF licence body.

## Status history

- 2026-05-01: Finalised by /record-retro (retrospective) — documents as-shipped licence metadata alignment after commit `78b3772`.
