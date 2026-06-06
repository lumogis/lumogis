# ADR-075: First wow moment path — readiness and dismissal (LUM-216)

**Status:** Finalised
**Created:** 2026-06-01
**Last updated:** 2026-06-01
**Decided by:** /explore --headless LUM-216; implementation verified `/verify-plan --headless` 2026-06-01

## Context

LUM-216 delivers the first deliberate “wow moment” in Lumogis Web: guided first-query and entity-discovery cards on the chat surface, server-owned readiness, once-only dismissal, and “Ask Lumogis about [entity]” shortcuts. The architectural question is how the web knows when entities are ready and where dismissal persists across devices — not card copy or styling.

## Decision

Extend the LUM-165 `/api/v1/me/*` pattern:

- Migration **028**: nullable **`users.wow_dismissed_at`**.
- **`GET /api/v1/me/wow-state`** returns `{ entities_ready, top_entities (≤5), wow_dismissed_at, onboarding_completed_at }` with **`Cache-Control: no-store`**.
- **`PATCH /api/v1/me/wow-state`** with `{ "dismissed": true }` only — idempotent **`COALESCE(wow_dismissed_at, NOW())`**, **`require_same_origin`** on PATCH (bearer bypass today, same as onboarding).
- **`entities_ready`** := count of household-visible **non-staged** entities ≥ 1 (`visible_filter` + `is_staged IS NOT TRUE`). This **differs** from `GET /entities`, which still lists staged rows.
- Web: **`features/wow/`** (`WowGate`, cards, `useWowState` with 4 s poll while not ready); mount **only on `ChatPage`**; chat prefill via `location.state` + `wowDismissOnSendRef` / `suppressDraftHydrateRef` (IndexedDB draft guard).
- **`AUTH_ENABLED=false`**: synthetic `entities_ready: true` and completed onboarding timestamp for QA.
- **Slice 1 defers** AC5 response entity chips (**LUM-205**) and full entity-browser ask rows (**LUM-161**); interim “Ask Lumogis about …” on **`EntityCardPanel`**.

## Alternatives Considered

- Client poll `GET /entities` with `len>0` — rejected as contract (Option 1 in exploration).
- SSE-only readiness — rejected as foundation; optional slice 2 on `/events`.
- `localStorage` dismissal — rejected (multi-device).

See `.cursor/explorations/LUM-216-first-wow-moment.md`.

## Consequences

**Easier**

- Authoritative readiness; testable mirror of `test_me_onboarding_routes.py`.
- Clean upgrade path to SSE invalidation of `['me','wow-state']` without changing PATCH semantics.

**Harder / constrained**

- One migration + service + routes to maintain.
- Two ACs remain on **LUM-161** / **LUM-205**; block edges in Linear still operator-owned (P2).

**Future chunks must know**

- Reuse **`askAboutEntity`** / ChatPage prefill path — do not fork.
- Optional **`identity_wizard_completed_at`** gate (**LUM-204**) is additive on `MeWowStateResponse`.

## Revisit conditions

- LUM-205 structured chat channel — AC5 chips consume it instead of post-response lookup.
- SSE first-batch event — drop poll loop when `/events` fans out readiness.
- `≥1 entity` mid-extraction misleading — add server debounce.

## Status history

- 2026-06-01: Draft created by /explore --headless LUM-216.
- 2026-06-01: Revised during /review-plan --arbitrate R1 — non-staged readiness.
- 2026-06-01: Finalised by /verify-plan --headless — implementation confirmed decision.
