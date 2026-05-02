# ADR 029: Self-hosted account password management (foundation)

**Status:** Finalised  
**Created:** 2026-04-26  
**Last updated:** 2026-04-26  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-04-26 (Composer)  
**Plan:** none — shipped before formal plan / verify cycle for this slice  
**Exploration:** *(maintainer-local only; not part of the tracked repository)*  
**Draft mirror:** *(maintainer-local only; not part of the tracked repository)*

## Context

Family-LAN and Lumogis Web shipped multi-user auth (`users` table, argon2id hashes, refresh cookie + access JWT). Operators still needed a **supported** way to rotate credentials without database surgery: **self-service** change for signed-in users, **admin** reset for another account, and **shell/CLI** recovery when the UI is unreachable.

This chunk landed as implementation-first work (commit `e8a4925`) without a dedicated `/create-plan` → `/verify-plan` file. Child-plan follow-up **LWAS-1** / reconciliation slug **`lumogis_password_management_foundation`** described the same intent. This ADR records the **as-built** contract so future plans (for example email-based forgot-password) do not contradict the v1 foundation.

## Decision

Lumogis v1 provides **three password mutation paths**, all backed by one service primitive that re-hashes with argon2id and **clears `users.refresh_token_jti`** for the affected user:

1. **Self-service:** `POST /api/v1/me/password` with `current_password` and `new_password`. Requires `AUTH_ENABLED=true`; returns **503** when auth is off (single-user dev mode). Wrong current password → **403** with detail `invalid credentials` (generic). Successful change invalidates refresh rotation for that user; existing access tokens expire at their normal TTL.

2. **Admin reset:** `POST /api/v1/admin/users/{user_id}/password` with `new_password`. Admin-only (`require_admin`), same-origin on write (`require_same_origin`). Does not require the target’s old password. Clears the target’s refresh JTI even if the account is disabled.

3. **Operator CLI:** from the **`orchestrator/`** directory (see `PYTHONPATH` / module layout in `orchestrator/scripts/reset_password.py`), run `python -m scripts.reset_password` with `--email` or `--user-id` and interactive password confirmation. Intended for trusted operators with filesystem/compose access to the orchestrator environment.

**Password policy (v1):** minimum length **12** characters (`services/users.py::MIN_PASSWORD_LENGTH`, `validate_password_policy`). Self-service path additionally rejects `new_password == current_password` with **`PasswordPolicyViolationError`**. Policy violations surface as **400** on HTTP routes.

**Explicit non-goals (v1):** no forgot-password email, no SMS, no time-limited reset tokens, no password history table, no Argon2 parameter rotation beyond what `argon2-cffi`’s `PasswordHasher` applies on new hashes.

### As-implemented surface (verified 2026-04-26)

| Surface | Location |
|--------|----------|
| Self-service route | `orchestrator/routes/me.py::change_my_password` |
| Admin route | `orchestrator/routes/admin_users.py::reset_user_password` |
| Service API | `orchestrator/services/users.py` — `change_own_password`, `admin_reset_user_password`, `cli_reset_password`, `_apply_new_password` |
| Wire models | `orchestrator/models/auth.py` — `MePasswordChangeRequest`, `AdminUserPasswordResetRequest`, `AckOk` |
| CLI | `orchestrator/scripts/reset_password.py` |
| Web helpers | `clients/lumogis-web/src/api/passwordManagement.ts` |
| Tests | `orchestrator/tests/test_api_v1_password_management.py` (+ auth phase fixtures) |

## Alternatives considered

- **Email magic-link reset only (no self-service)** — Rejected for v1: requires SMTP, token store, and abuse handling; deferred until a product decision funds outbound email.
- **Admin reset without JTI clear** — Rejected: would leave stolen refresh cookies valid after a known compromise window.
- **503 vs 404 for `/me/password` when `AUTH_ENABLED=false`** — Shipped: **503** with explicit “dev mode” detail so clients do not treat the route as missing.

## Consequences

- **Easier:** Operators have a documented, tested path for lockouts: CLI or another admin account.
- **Easier:** Lumogis Web can rely on stable OpenAPI shapes for both POST bodies.
- **Harder:** Email-based recovery must be designed as an **additive** flow (new routes, rate limits, audit) and must not weaken the current admin/CLI gates.
- **Future chunks must know:** Access JWT revocation is **unchanged** — only refresh JTI is cleared; multi-access-token revocation remains separate work (see ADR 012, portfolio FP-006).

## Revisit conditions

- Outbound **email/SMS** reset is in scope → new implementation plan; may introduce **`lumogis_forgot_password_email_reset`** (or equivalent) as portfolio row.
- **Enterprise password policy** (rotation cadence, complexity rules) → extend `validate_password_policy` and align Pydantic `Field` constraints with service checks.

## Status history

- **2026-04-26:** Finalised by /record-retro (retrospective) — as-built record for foundation shipped in `e8a4925`.
- **2026-04-26:** Documentation correction — CLI entry point aligned with the shipped module (`python -m scripts.reset_password` with working directory `orchestrator/`, not `python -m orchestrator.scripts.reset_password`).
