# ADR-059: First-run onboarding and zero states (LUM-165)

**Status:** Finalised
**Created:** 2026-05-22
**Last updated:** 2026-05-22
**Decided by:** /explore --headless LUM-165 (claude-opus-4.7); implementation verified `/verify-plan --headless` 2026-05-22

## Context

Lumogis Web showed a blank chat on first login with no guidance. LUM-165 specifies a one-time, skippable linear modal (not a tooltip tour) plus useful empty states, starting with chat. The codebase already had `CopyOnceModal` for ESC, initial focus, and restore-on-close — but not a Tab-cycle focus trap.

## Decision

Ship **LUM-165** as a hand-rolled `OnboardingModal` with **`ModalFrame`** providing overlay, **Tab-cycle focus trap**, `#root` **inert** / **aria-hidden** while open, plus shared **`EmptyState`** for chat (LUM-160 / LUM-161 consume the same contract later). Backend: nullable **`users.onboarding_completed_at`** (migration **025**) and **`GET` / `PATCH /api/v1/me/onboarding`** with **`require_same_origin`** on **`PATCH`**. **`AUTH_ENABLED=false`** returns a synthetic non-null `completed_at` with **`Cache-Control: no-store`** so dev stays branch-free. **No** new npm dependency for the modal stack. **No** admin on-behalf completion field.

## Alternatives Considered

See draft history in `.cursor/explorations/LUM-165-first-run-onboarding-zero-states.md` — Radix Dialog, tour libraries, headless wizard engines, and localStorage-only completion were rejected for cost, issue fit, or cross-device correctness.

## Consequences

**Easier**

- LUM-160 / LUM-161 can reuse **`EmptyState`** (`helperText`, ordered `actions`, at most one `primary`).
- Two thin routes on existing `/api/v1/me` router; idempotent **`PATCH`** does not bump timestamp on repeat.

**Harder / closed off**

- Tooltip / spotlight first-run tours remain out of scope per issue.
- “Replay onboarding” (`completed: false`) remains a schema-widening follow-up (OpenAPI + **LUM-302** classifier posture) — not shipped in v1.

**Future chunks must know**

- Public **`EmptyState`** prop is **`helperText`**, not `description`.
- Onboarding completion is per **`users.id`**, not per household.

## Revisit conditions

- Design-system adoption (Radix / shadcn) — converge `ModalFrame` / `CopyOnceModal`.
- Telemetry evidence that skip-heavy flows need replay — add Settings affordance + `PATCH` semantics.
- React / platform dialog primitive migrations.

## Status history

- 2026-05-22: Draft created by `/explore --headless LUM-165`.
- 2026-05-22: Revised during `/review-plan --arbitrate` R1 — focus trap + `helperText` alignment.
- 2026-05-22: Finalised by `/verify-plan --headless` — implementation matches decision; draft mirror at `.cursor/adrs/LUM-165-first-run-onboarding-zero-states.md`.
