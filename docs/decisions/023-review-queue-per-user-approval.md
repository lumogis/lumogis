# ADR 023: Review queue per-user approval scope (audit B9)

**Status:** Finalised
**Created:** 2026-04-21
**Last updated:** 2026-04-21
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-04-21 (Claude Opus 4.7)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** *(maintainer-local only; not part of the tracked repository)*
**Draft mirror:** *(maintainer-local only; not part of the tracked repository)*

## Context

The unified review queue (Pass 4a KG quality pipeline) exposes `POST /review-queue/decide` so operators can merge ambiguous entities, promote/discard staged entities, and resolve constraint violations. In multi-user (family-LAN) mode, allowing **any** authenticated user to act on **any** queue item would let one household member change another’s pending merge or violation resolution.

This gap was tracked in the private multi-user audit response as **row B9** (`review_queue_per_user_approval_scope` in code; narrative reference `docs/private/MULTI-USER-AUDIT-RESPONSE.md` row B9). The work shipped without a prior `.plan.md` or `/verify-plan` for this specific slice. This ADR records the as-built contract.

## Decision

`POST /review-queue/decide` **requires a logged-in user** (`Depends(require_user)`), resolves the **originating data owner** from the per-item database row (never from the request body for authorization), and allows a **non-admin** to act **only** when `originating_user_id == ctx.user_id`. **Admins** may act on any user’s item. When an admin acts on an item that belongs to another user, the `review_decisions` row is still scoped to the **originating** `user_id`, and the actor’s id is stored in the JSONB `payload` as `acted_by_user_id` when (and only when) it differs from that owner. **No** Postgres schema migration: the additive field lives in existing `review_decisions.payload` JSONB.

`GET /review-queue` (unified / admin listing) is **out of scope** for this decision — B9 is strictly about **authorization to mutate** via `decide`, not about who may see the global queue.

### As-implemented surface (verified 2026-04-21)

- **Route:** `orchestrator/routes/admin.py::review_queue_decide` — `POST /review-queue/decide`, `DecideRequest` with `item_type`, `item_id`, `action`, optional `user_id: str = "default"`.
- **Auth:** `Depends(require_user)`; unauthenticated family-LAN calls receive **401** from middleware (see tests).
- **Originating owner resolution:** `orchestrator/routes/admin.py::_resolve_originating_user_id(meta, item_type, item_id) -> str | None`
  - `ambiguous_entity` → `review_queue` by `id`
  - `staged_entity` → `entities` by `entity_id`
  - `constraint_violation` / `orphan_entity` → `constraint_violations` by `violation_id`
  - return `str(row.get("user_id") or "default")` when a row exists; `None` when missing (→ **404**)
- **Non-admin + spoofing:** If `not is_admin` and `body.user_id` (stripped) is set to something other than `""` / `"default"` / `ctx.user_id` → **403** `cannot act on behalf of another user` before ownership `fetch_one` (defence in depth; tests assert zero `fetch_one` calls).
- **Non-admin B9:** If `not is_admin` and `originating_user_id != ctx.user_id` → **403** `review item belongs to a different user` (WARNING log; no `execute` on the failure path; tests assert `execute` count 0 for cross-user).
- **Admin on behalf:** All successful branches call `_insert_review_decision(..., user_id=originating_user_id, acted_by_user_id=ctx.user_id)`; `_insert_review_decision` copies `acted_by_user_id` into the JSON payload only when it differs from `user_id` (so self-approval has no `acted_by_user_id` key).
- **Body `user_id` vs DB:** Admin `body.user_id` does not override the originating user for writes — tests pin that a conflicting `user_id: "bob"` with an `alice` item still scopes decisions and updates to `alice`.
- **Tests:** `orchestrator/tests/test_review_queue.py` — `TestReviewQueuePerUserApprovalScope` (B9) uses `auth_app` + JWT minting, `MagicMock` store, and covers owner success, cross-user 403, spoof 403, admin + `acted_by_user_id`, ignored admin body field, 401, 404, and 400 before DB.

### What was NOT changed (explicitly deferred)

- **No** change to `GET /review-queue` / `?source=all` admin-read contract (remains a cross-user admin/audit surface per existing plan text).
- **No** new `review_decisions` column for actor id — JSONB `payload.acted_by_user_id` only.
- **No** new env vars, routes, or migrations.
- **No** expansion of B9 tests to every `item_type` / `action` combination — the group uses `constraint_violation` + `suppress` as the primary matrix; the authorization layer is item-type-agnostic and shared by all branches in `review_queue_decide`.

## Alternatives considered

- **Not chosen at ship time — keep `require_admin` only for `decide`:** would preserve single-admin-operator LAN installs but would block non-admin end users from ever approving their *own* queue items in family mode; rejected in favour of per-user self-service plus admin override.
- **Not chosen at ship time — trust `body.user_id` for scoping for non-admins:** would re-open trivial privilege escalation; rejected — originating owner is always DB-sourced.
- **Not chosen at ship time — separate `review_decisions.acted_by_user_id` column:** more queryable, but requires migration and dual-write discipline; JSONB in payload matches “audit blob” usage and needed no migration.

## Consequences

- **Easier:** Clear pattern for any future per-user “decide” endpoint: `resolve owner from row` → `compare to ctx` unless admin → `insert audit with owner scope` + `acted_by` in JSON when needed.
- **Harder:** Every new `item_type` in `_REVIEW_QUEUE_ITEM_TYPES` **must** add a branch to `_resolve_originating_user_id` or B9 will incorrectly 404. Add a test when adding a type.
- **Future chunks must know:** The unified queue **read** path can stay admin-global while the **write** path is per-item-owner — that asymmetry is intentional until a product request narrows `GET` scope.

## Revisit conditions

- If the product needs non-admins to see **only** their slice of the unified queue, add a dedicated plan (new query filters or routes) — do not conflate with B9.
- If `review_decisions` analytics need indexed lookup by actor, add a generated column or materialized view — revisit payload-only storage.
- If `NULL` `user_id` in source rows becomes common, revisit `"default"` coercion vs explicit error.

## Status history

- 2026-04-21: Finalised by /record-retro (retrospective) — as-built documentation for `review_queue_per_user_approval_scope` (audit B9).
