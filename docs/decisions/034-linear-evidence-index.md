# ADR 034: Linear ↔ repo evidence index (maintainer register)

> Status: Needs update
> Last reviewed: 2026-05-24
> Verified against commit: 50f43b8
> Notes: This register remains the canonical description of the Linear evidence index, but the **ADR number collides** with `docs/decisions/034-agent-harness-foundation-terminology-and-boundaries.md`. Filename prefixes **049–064** are already in use under `docs/decisions/` (including **two `053-*.md`**, **three `059-*.md`**, **two `060-*.md`**, **two `061-*.md`**, **two `063-*.md`**, **three `064-*.md`**). A coordinated rename must use a **non-colliding** slug (for example **`065-*.md`** or **`065-lum-86-*.md`**) — **not** **053** / **061** / **063** / **064**. Coordinate with the **046** pair rename if multiple new numbers are needed in one pass—see `docs/_librarian/docs-inventory.md`. Paths under **`.cursor/`** and **`scripts/linear/`** exist when **`lumogis-app/.cursor`** is symlinked to **lumogis-devtools** per **`AGENTS.md`** (absent in product-only checkouts).

**Status:** Finalised  
**Created:** 2026-05-07  
**Last updated:** 2026-05-07  
**Decided by:** `.cursor/plans/LUM-86-linear-evidence-index.plan.md` + `/verify-plan`  
**Finalised by:** /verify-plan 2026-05-07 (Composer)  
**Linear:** [LUM-86](https://linear.app/lumogis/issue/LUM-86/consolidate-repo-plansexplorations-linear-evidence-index) (CSV **CLOSEOUT-EVIDENCE-2026-05-03**)  
**Plan:** `.cursor/plans/archived/LUM-86-linear-evidence-index.plan.md`  
**Design spec:** `cursor/reports/linear-repo-evidence-index-design-2026-05-03.md`  
**Exploration:** none (devtools governance artefact; spec-backed)  
**Draft mirror:** `.cursor/adrs/linear_evidence_index.md` (pointer to this file)

## Context

**ADR 033** (**plan ↔ Linear** linkage / **LUM-9**) enforces classification on individual **plan** files; parallel **exploration ↔ Linear** tooling (**LUM-10**) covers **explorations**. Operators still need an **additive, read-mostly join table** spanning **many `LUM-###`** × **many evidence paths** (plans, explorations, portfolio, docs, reports) without turning that markdown into backlog truth or replacing LUM-9/LUM-10 checkers.

## Decision

1. **Canonical register:** Committed **`cursor/evidence/linear-evidence-index.md`** — markdown pipe-table, **priority and status live in Linear**, not duplicated as execution queue in-repo (banner + maintainer **`cursor/evidence/README.md`** spell this out).

2. **Read-only tooling (lumogis-devtools, no Linear API keys in new scripts):**
   - **`scripts/linear/check_linear_evidence_index.mjs`** — validates table shape (including separator-row skip), enums, **`LUM-\d+`**, **`cursor/`** vs **`docs/`** containment (`existsSync` before `realpathSync`), **`--json`** / **`--strict-docs`**.
   - **`scripts/linear/seed_evidence_index_from_csv.mjs`** — joins **`cursor/reports/linear-id-map-2026-05-03.csv`** to **`linear-issues-export.json`** (UUID → identifier); stdout only.
   - **`scripts/linear/suggest_evidence_index_rows.mjs`** — suggests rows from **`cursor/plans/**/*.plan.md`** + recursive **`cursor/explorations/**/*.md`**.

3. **`node:test`** fixtures under **`scripts/linear/__fixtures__/linear-evidence-index/`** and **`linear_evidence_index.test.mjs`** as regression gate.

4. **Relationship to LUM-9/LUM-10:** The index **aggregates** and **documents** linkage; **`check_plan_linear_linkage.mjs`** / **`check_exploration_linear_linkage.mjs`** remain authoritative for **single-file** classification. Index **Role** aligns with grandfather **`evidence_role`** (**including `superseded`**) where seeded from registry-backed rows.

## Alternatives considered

- **Database or Linear custom fields alone** — rejected for offline maintainer ergonomics and diffable evidence in-repo.  
- **Replace LUM-9/LUM-10 with the index** — rejected; violates plan scope and loses per-file gates.  
- **CI-required index on every PR** — explicitly **deferred** (design § follow-up register / LUM-87 style automation).

## Consequences

- **Positive:** One place for cross-artifact **`LUM-###`** ↔ path lookup; repeatable refresh via seed/suggest helpers; deterministic validation for typos/path drift where rows exist.
- **Negative:** Manual stewardship (operators merge seed stdout, reconcile unmappable CSV rows such as FP-class repo-only refs); **`docs/decisions/**` rows warn when product checkout or paths are stale unless **`--strict-docs`**.

## Revisit conditions

- **`drift_check.mjs`** (or successors) optionally subprocess the index checker when automation centralises (**link existing drift / governance issues** rather than reinventing trackers).  
- **`/verify-plan` auto-append** of index rows (**separate Linear child** when implemented — traceability anchored on programme / LUM-86, see plan **Follow-up register**).

## Status history

- 2026-05-07: Finalised by /verify-plan — scripts + **`cursor/evidence/*`** landed in **lumogis-devtools**; product repo records this ADR only.
