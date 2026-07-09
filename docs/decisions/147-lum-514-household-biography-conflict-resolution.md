# ADR-147: Household biography — multi-member conflict resolution (LUM-514)

**Status:** Finalised

**Created:** 2026-06-30

**Last updated:** 2026-06-30

**Decided by:** `/explore --headless LUM-514`; implemented per `.cursor/plans/LUM-514-household-biography-conflict-resolution.plan.md`

**Finalised by:** /verify-plan 2026-06-30 (Composer)

**Plan:** `.cursor/plans/LUM-514-household-biography-conflict-resolution.plan.md`

**Exploration:** `.cursor/explorations/LUM-514-household-biography-conflict-resolution.md`

**Draft mirror:** `.cursor/adrs/LUM-514-household-biography-conflict-resolution.md`

**Linear:** [LUM-514](https://linear.app/lumogis/issue/LUM-514/household-biography-multi-member-conflict-resolution-for-shared-scope)

**Parent:** [LUM-201](https://linear.app/lumogis/issue/LUM-201) (household biography two-layer model)

## Context

LUM-201 v2 committed to building the household/shared biography layer (`biography_pins.scope ∈ {personal, shared}`; `household_biography_synthesis`). It deferred **what happens when household members' shared-scope facts disagree** to LUM-514.

Constraints:

- **No silent data loss** — curated household facts must not be silently overwritten.
- **ADR 015 projection model** — shared rows are per-member attributable projections; conflict is synthesis-time, not a storage write-race.
- **Household RBAC** — `orchestrator/authz.require_admin` for resolution authority; `AUTH_ENABLED=false` is single-user no-op.
- **Local-first** — detection on Postgres; no cloud arbitration.

## Decision

Adopt **surface-conflict-for-review with provenance/attribution** (Option C):

1. **Equivalent pins** (same fact group + same normalised value) → de-duplicate silently.
2. **Divergent pins** on the same household fact → detect conflict; default synthesis is **represent-both with attribution** (`user_id` labels, sorted ASC). Household admin may **confirm_one**, **keep_both**, or **dismiss**. Losers are **archived in audit**, not deleted.
3. **Resolution authority** = household admin. Single distinct author → strict no-op.

**Shipped in LUM-514:**

- Pure policy module `orchestrator/services/biography_conflict.py` (`detect_conflicts`, `format_represent_both`, `apply_resolution_with_pins`).
- Postgres audit table `biography_conflict_resolutions` (migration **043**).
- Admin API `GET/POST /api/v1/biography/conflicts` (`require_user` list/detail; `require_admin` resolve).
- Internal integration point `detect_and_persist_open_conflicts` for LUM-516.
- Private acceptance handoff `docs/private/specs/biography-conflict-acceptance.md`.

**Grouping key:** `(category, domain, subject_key)` where `subject_key` is normalised `subject_entity_id` or `subject_text`. `category=identity` exempt; subjectless pins not eligible; personal scope excluded.

**Review flag:** `requires_review=true` for `logistics`, `focus`, `relationship`; `preference` and `other` → represent-both only.

## Alternatives considered

See exploration `.cursor/explorations/LUM-514-household-biography-conflict-resolution.md` — LWW (primary), silent admin precedence, CRDT/OT, destructive personal-source mutation, and cloud LLM arbitration were rejected.

## Consequences

**Easier:**

- Frozen contract before LUM-515/516/518 cut schema, synthesis, and UI.
- Honest household synthesis path; audit trail in Postgres.
- Single-user deployments pay nothing (no-op).

**Harder:**

- LUM-516 must call detection before synthesis and inject `represent_both_line`.
- LUM-518 must wire review UI to resolve API.
- LUM-515 must align `biography_pins` columns with `BiographyPinSnapshot`.

**Blocks (Linear):** LUM-515, LUM-516, LUM-518 consume this module; do not ship silent LWW aggregation in LUM-516.

## Revisit conditions

- Per-category strategy table if households need finer rules.
- High conflict volume → prioritised review queue.
- Per-item ACL (ADR 015 revisit) changes authority model.
- Temporal/contextual facts → bitemporal fields for "keep both with context window".

## Status history

- 2026-06-30: Finalised by /verify-plan — implementation confirmed; migration **043** owned by LUM-514 (revised from draft ADR's LUM-515/`035` placement).
- 2026-06-30: Draft created by `/explore --headless LUM-514`; revised at `/review-plan --arbitrate` R1.
