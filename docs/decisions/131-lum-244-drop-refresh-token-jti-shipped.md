# ADR 131: Drop legacy `users.refresh_token_jti` column (LUM-244)

**Status:** Finalised

**Created:** 2026-06-24

**Last updated:** 2026-06-24

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-24

**Plan:** none — shipped on `claude/blissful-cerf-293p51` before formal plan / verify for this slice

**Exploration:** `.cursor/explorations/lum-244-drop-refresh-token-jti-retro.md`

**Draft mirror:** `.cursor/adrs/lum_244_drop_refresh_token_jti.md`

**Linear:** [LUM-244](https://linear.app/lumogis/issue/LUM-244) (child of [LUM-29](https://linear.app/lumogis/issue/LUM-29); umbrella [LUM-51](https://linear.app/lumogis/issue/LUM-51))

**Extends:** [ADR 041](041-jwt-access-token-revocation-multi-device-sessions.md) (closes the dual-write downgrade window)

## Context

ADR 041 (LUM-29, shipped 0.4.0) replaced the single-column refresh contract (`users.refresh_token_jti`) with the `auth_sessions` table and `users.token_version`. The legacy column was retained for one-release downgrade safety and guarded by a CI grep gate (`scripts/check_refresh_token_jti_guard.py`) that forbids new production reads or writes.

LUM-244 tracks removal of that column after a safe release window. Six releases shipped (0.5.0 → 0.8.0) with zero production references. This retrospective records the as-shipped drop.

## Decision

1. **Drop the column** via `postgres/migrations/037-drop-users-refresh-token-jti.sql` (`DROP COLUMN IF EXISTS refresh_token_jti` on `users`).
2. **Retain the grep gate** — production orchestrator modules must not mention `refresh_token_jti` (tests and migration fixtures exempt).
3. **Refresh-side state** remains exclusively in `services/auth_sessions.py` + `users.token_version` per ADR 041.

## Alternatives considered

- **Keep the column indefinitely:** rejected — schema drift and operator confusion; downgrade below 0.4.0 is no longer a supported path.
- **Drop without grep gate:** rejected — gate is cheap insurance against accidental reintroduction.
- **New sessions table / auth redesign:** not in scope — LUM-244 is column cleanup only.

## Consequences

**Easier:**

- `users` table matches runtime auth model; no dead column in exports or schema docs.
- ADR 041 dual-write revisit condition is closed.

**Harder:**

- Operators cannot downgrade to a pre-0.4.0 auth stack that expected `refresh_token_jti` (acceptable).

**Future chunks must know:**

- Do not read or write `users.refresh_token_jti`; use `auth_sessions` APIs.
- Personal data export continues to omit session metadata per ADR 041.

## Revisit conditions

- Reopen if grep gate fails in CI (indicates reintroduction).
- Do not resurrect the column without a new ADR and migration design.

## As-implemented surface

| Artifact | Path |
| --- | --- |
| Migration | `postgres/migrations/037-drop-users-refresh-token-jti.sql` |
| CI guard | `scripts/check_refresh_token_jti_guard.py`; `make refresh-token-jti-guard` |
| Tests | `orchestrator/tests/test_drop_refresh_token_jti_migration.py` |

## Testing retrospective

- **Added:** `test_drop_refresh_token_jti_migration.py` — drop, idempotent re-apply, noop when column already absent.
- **Run:** `pytest orchestrator/tests/test_drop_refresh_token_jti_migration.py`; `python scripts/check_refresh_token_jti_guard.py`.
- **Results:** migration tests skip without Postgres; grep guard passes in dev checkout.
- **Gap:** operator `make update` on a live volume with the column is the real-world proof (not blocking retro).

## Linear linkage (Product OS)

- **Existing issue:** LUM-244
- **Recommended closure:** `/linear-update apply-closure LUM-244 --done` with evidence SHA on `dev` and this ADR.

## Status history

- 2026-06-23: Shipped on `claude/blissful-cerf-293p51` (migration 037 + ADR 041 amend).
- 2026-06-24: Finalised by `/record-retro` (retrospective ADR + exploration on integrated `dev`).
