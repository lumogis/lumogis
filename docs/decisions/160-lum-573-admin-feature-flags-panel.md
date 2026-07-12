# ADR-160: Admin feature flags read-only panel (LUM-573)

**Status:** Finalised
**Created:** 2026-07-10
**Last updated:** 2026-07-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-10 (Composer)
**Plan:** none — shipped on `claude/design-context-status-w7xez3`
**Exploration:** `.cursor/explorations/archived/admin_feature_flags_panel_retro.md`
**Draft mirror:** `.cursor/adrs/lum_573_admin_feature_flags_panel.md`
**Builds on:** ADR-146 (LUM-126 feature-flag registry)

**Linear:** [LUM-573](https://linear.app/lumogis/issue/LUM-573) (child of LUM-570 egress POC programme)

## Context

LUM-126 shipped an experimental feature-flag registry surfaced via orchestrator diagnostics. Operators lacked a Lumogis Web admin view of which flags are active and what they mean — especially defence-in-depth flags like `EGRESS_GUARD`. LUM-573 adds read-only visibility without duplicating registry logic in the UI.

## Decision

1. **Route** — `/admin/feature-flags` (`AdminFeatureFlagsView`) admin-gated like other Lumogis Web admin panels.
2. **API client** — `clients/lumogis-web/src/api/featureFlags.ts` reads the existing experimental flags registry endpoint.
3. **Env-only v1** — panel does **not** mutate flags; operators still edit env / compose for toggles.
4. **Honesty copy** — `EGRESS_GUARD` documented as bypassable defence-in-depth (not a hard security boundary).
5. **Tests** — Vitest in `AdminFeatureFlagsView.test.tsx` (2 cases).
6. **Nav** — link in `AdminNav.tsx`.

## Alternatives considered

- **Mutating admin UI** — deferred until LUM-126 gains a runtime mutation API with audit.
- **Duplicate registry in frontend** — rejected; single source of truth stays orchestrator-side.

## Consequences

**Positive:** Closes LUM-570 follow-up visibility gap without new backend contract.

**Limits:** No Playwright admin navigation e2e (P2 — acceptable for read-only table).

## Revisit conditions

- When LUM-126 registry gains runtime mutation API, replace read-only table with save flow + audit events.

## Testing retrospective

| Layer | Command | Result |
|-------|---------|--------|
| Vitest | `npm test -- --run tests/features/admin/AdminFeatureFlagsView.test.tsx` | **2 passed** |

## Linear linkage (Product OS)

- **LUM-573** — scope complete; apply `/linear-update apply-closure LUM-573 --done`.
