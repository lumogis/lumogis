# ADR-143: Lumogis Web lint gate — eslint `--max-warnings 0` green (LUM-546)

**Status:** Finalised

**Created:** 2026-06-29

**Last updated:** 2026-06-29

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-29 (Composer)

**Plan:** none — filed from LUM-520 verify follow-up; shipped on `claude/lum-546-web-lint-gate`

**Exploration:** `.cursor/explorations/lum_546_lumogis_web_lint_gate_retro.md`

**Draft mirror:** `.cursor/adrs/lum_546_lumogis_web_lint_gate.md`

**Linear:** [LUM-546](https://linear.app/lumogis/issue/LUM-546)

**Related:** [ADR-140](140-lum-520-admin-panel-completion.md) (LUM-520 admin panel — lint debt filed during verify)

## Context

LUM-520 verify noted pre-existing **`clients/lumogis-web`** lint debt: `eslint --max-warnings 0` failed on a stale `eslint-disable` in `AppErrorBoundary` and four `react-refresh/only-export-components` warnings from helper exports co-located with React components. LUM-546 tracked Release/Export Hygiene to green the gate without changing product behaviour.

## Decision

**Extract display helpers into dedicated modules** so view components export only components, and remove the stale disable directive. No ESLint rule relaxation.

As-built changes:

- **`adminUsersDisplay.ts`** — `roleLabel`, `formatLastActive` extracted from `AdminUsersView.tsx`.
- **`mcpTokenDisplay.ts`** — MCP token display helpers extracted from `MeMcpTokensView.tsx` / admin MCP views.
- **`AppErrorBoundary.tsx`** — remove unused eslint-disable.
- **`DocumentUploadPanel.tsx`** — minor lint-safe adjustment.
- Tests import helpers from the new modules.

## Alternatives considered

- **Relax `react-refresh` rule or raise max-warnings:** rejected — would hide real fast-refresh hazards and weaken the CI gate.
- **Inline eslint-disable per export:** rejected — debt accumulation; extraction is the established pattern.

## Consequences

**Easier:** `npm run lint` in `clients/lumogis-web` is a reliable local/CI gate again.

**Harder:** Small module split — future admin/me display helpers should land in `*Display.ts` files, not in view modules.

## Revisit conditions

- If new admin/me panels add non-component exports, enforce extraction before merge (lint gate).

## Linear linkage (Product OS)

- **LUM-546** — recommend **`/linear-update apply-closure LUM-546 --done`** after merge evidence posted.

## Testing retrospective

| Item | Detail |
| --- | --- |
| Commands | `clients/lumogis-web npm run lint`; `vitest run tests/features/admin/AdminUsersView.test.tsx` |
| Results | lint **0 warnings**; AdminUsersView **17 passed** |
| Gaps | Full lumogis-web vitest suite not re-run this pass (scoped to touched admin tests + lint) |

## Status history

- 2026-06-29: Finalised by /record-retro; merged to `dev` at `15cb0ff43`.
