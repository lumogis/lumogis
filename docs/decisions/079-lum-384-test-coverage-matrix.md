# ADR-079: LUM-384 — Four-tree TEST-COVERAGE-MATRIX contract

**Status:** Finalised
**Created:** 2026-06-03
**Last updated:** 2026-06-04
**Decided by:** /explore + /verify-plan (LUM-384)

## Context

Lumogis needs a durable map of **product behaviours → test evidence**, separate from **how to run suites** (`scripts/debug/inventory.tsv`, LUM-377) and **changed-line CI** (LUM-379). Linear LUM-384 originally proposed a single OpenHuman-style matrix with ≥30 rows; the product surface and AGPL/private export split require more.

## Decision

Adopt **four Markdown matrices** split by export boundary:

| Matrix | Path | ID prefix |
| --- | --- | ---: |
| Core | `docs/testing/TEST-COVERAGE-MATRIX-core.md` | `1.x.x` |
| Web | `docs/testing/TEST-COVERAGE-MATRIX-web.md` | `2.x.x` |
| KG (private) | `docs/private/testing/TEST-COVERAGE-MATRIX-kg.md` | `3.x.x` |
| Desktop (private) | `docs/private/testing/TEST-COVERAGE-MATRIX-desktop.md` | `4.x.x` |

Index: `docs/testing/README.md`; private maintainer note: `docs/private/testing/README.md`.

**Statuses:** ✅ / 🟡 / ❌ / 🚫 — v1 seeded by **code-first audit** (routes, `App.tsx`, capabilities-shaped features) with test-file grep; archived plans supply optional **LUM-###** hints only.

**Ongoing maintenance:** rows added or updated when **`/verify-plan`** closes a feature plan (**LUM-427**); not on every drive-by PR. Format CI: **`scripts/check-coverage-matrix.mjs`** + **`scripts/feature-ids.json`** (**LUM-429**, `make coverage-matrix-check`).

**Process glue:** `CONTRIBUTING.md`, `.github/pull_request_template.md`, `docs/testing/automated-test-strategy.md`. Re-seed helper: `scripts/testing/_lum384_seed_matrices.py` (maintainer-only, not CI).

## Alternatives considered

- Single monolithic matrix — rejected (export boundary + size).
- Plan-index-only seeding — rejected as sole source; code audit is authoritative for features.
- PR-required manual matrix edits — rejected for steady state; verify-plan owns updates.
- OpenHuman-style parser shipped in **LUM-429** (`scripts/lib/coverage-matrix-parser.mjs`, four-file layout).

## Consequences

- **Easier:** Release planning, honest visibility of ❌ rows, LUM-379/385 cross-links.
- **Harder:** Matrix rows only stay honest if every feature verify run performs **Step 7c** (`.cursor/skills/verify-plan/SKILL.md`).
- **Public export:** only core + web matrices and public README links; `docs/private/testing/*` stripped.

## Revisit conditions

- **LUM-427** implemented as verify-plan **Step 7c** — keep skill and CI gate aligned when matrix contract changes.
- Keep **`feature-ids.json`** in sync when matrix IDs change (`node scripts/check-coverage-matrix.mjs --write-catalog`).
- Revisit taxonomy when `docs/capabilities.md` H2 structure changes materially.

## Status history

- 2026-06-03: Draft created by /explore (LUM-384)
- 2026-06-03: Finalised by /verify-plan — four matrices shipped on `dev`; code-audit v1 seed
- 2026-06-04: **LUM-428** — strict citations + active/archived plan cross-check (`_lum384_plan_audit.py`, `_lum428_audit_matrix_citations.py`); supplemental rows scoped to owning plan file only
- 2026-06-04: **LUM-427** — verify-plan **Step 7c** documents per-chunk matrix row updates; **LUM-429** format CI (`make coverage-matrix-check`)
- 2026-06-06: **LUM-385** — `docs/RELEASE-MANUAL-CHECKLIST.md` shipped with **MS-001…MS-010**; core/web/kg matrix 🚫 stubs cite concrete **MS-###**; parser accepts `MS-\d{3}` or `MS-TBD`; Hub signing rows remain **MS-TBD pending LUM-408**
