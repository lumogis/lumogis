# ADR-072: Compose multi-bind generator for ingest_paths[1..n] (LUM-401)

> Status: Active (numbering conflict)
> Last reviewed: 2026-06-14
> Verified against commit: a36f022
> Notes: **`docs/decisions/072-lum-398-client-only-overlay.md`** also claims **ADR 072** in its title. Resolve by renumbering one document and sweeping references. Filename prefixes **053–097** are already taken (duplicate clusters on **053**, **059**, **060**, **061**, **063**, **064**, **072**, **074**, plus **`065-lum-320-*.md`** through **`097-lum-470-pip-dependency-hash-pinning.md`**). Pick a **non-colliding** new slug (for example **`098-*.md`**) when renumbering—coordinate with any **`034-linear-evidence-index.md`** / **046** / **074** pair rename in the same pass—see `docs/_librarian/docs-inventory.md`.

**Status:** Finalised
**Created:** 2026-05-29
**Last updated:** 2026-05-30
**Decided by:** `/explore --headless` (claude-opus-4-8-thinking-medium); implemented per `.cursor/plans/LUM-401-compose-multibind-generator.plan.md`; finalised by `/verify-plan` (2026-05-30)

## Context

ADR-071 (LUM-397) shipped multi-root ingest but bound only index 0 (`FILESYSTEM_ROOT` → `/data:ro`). Indices 1..n received `INGEST_PATHS` env entries without matching compose bind mounts, forcing hand-edited overrides and container-visible paths in settings. LUM-401 closes that gap with a tiered generator integrated into admin `PUT /settings` and live `COMPOSE_FILE` reads in stack-control.

## Decision

Generate extra binds as a **`docker-compose.override.yml` fragment** (Compose merge) with host→container derivation (`/data-1`, …):

- **Snippet tier (always):** when `len(ingest_paths) >= 2`, return `ingest_compose_snippet` for operator copy.
- **Auto tier (when `/project/.env` writable):** text-splice managed volume lines between `# lumogis:ingest-binds-begin/end` markers; structural YAML validation only (no Docker CLI in orchestrator); chain `COMPOSE_FILE` with **`:docker-compose.override.yml`** only after validation passes.
- **Bind sources:** `browse_path_to_bind_source()` strips `/host/…` to daemon paths (pure string rules at translate time; mount presence checked only at validation).
- **Shrink:** single-path PUT strips managed block; delete empty override and unchain `COMPOSE_FILE`.

Implementation: `orchestrator/compose_ingest_binds.py`, `orchestrator/config.py`, `orchestrator/routes/admin.py`, `stack-control/main.py` (`_current_compose_file()`).

## Alternatives Considered

- Edit base `docker-compose.yml` in place — rejected (tracked public file).
- `python-on-whales` / `docker-py` — rejected (redundant with stack-control subprocess).
- Named volume / broad `/host:ro` — rejected (wrong ingest semantics).

Full detail: `.cursor/explorations/archived/LUM-401-compose-multibind-generator.md`.

## Consequences

- **Easier:** operators add extra folders using host paths; auto tier + restart mounts binds when platform override exists.
- **Must know:** stack-control uses explicit `-f` chain — override merges only when `COMPOSE_FILE` lists it; generated override is gitignored host state (not in AGPL export).
- **Contract:** index≥1 paths are host-visible browse paths; container paths are derived in `INGEST_PATHS`.
- **Verification:** live second-bind proof remains **LUM-400** E2E; desktop snippet UX **LUM-402**.

## Relation to other decisions

- **ADR-071** — parent multi-root ingest; § Consequences amended to point here.
- **ADR-073** — LUM-400 restart E2E extends for second bind after this generator ships.

## Status history

- 2026-05-29: Draft created by `/explore --headless` (LUM-401).
- 2026-05-30: Revised during `/review-plan --arbitrate` R1.
- 2026-05-30: Finalised by `/verify-plan` — implementation confirmed.
