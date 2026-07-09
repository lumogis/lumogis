# ADR-154: Household invite flow (multi-user onboarding) — LUM-186

> Status: Needs update  
> Last reviewed: 2026-07-07  
> Verified against commit: 6c80e10  
> Notes: **LUM-577** shipped the admin invite-time **`allows_shared`** toggle with per-user shared-scope enforcement — the Decision v1 bullets below still describe the toggle as hidden.

**Status:** Finalised
**Created:** 2026-07-05
**Last updated:** 2026-07-05
**Decided by:** `/explore --headless` LUM-186; implemented `agent/lum-186`; finalised by `/verify-plan --headless` 2026-07-05
**Plan:** `.cursor/plans/LUM-186-household-invite-flow.plan.md`
**Linear:** [LUM-186](https://linear.app/lumogis/issue/LUM-186/multi-user-onboarding-household-member-invitation-flow-scope)

## Context

LUM-334 (Done) and LUM-520 (Done) shipped household RBAC and the admin user panel but deferred self-service invites (`AdminUsersView` showed “coming soon”). LUM-186 closes that gap: admins mint single-use invite links; new members redeem without a pre-created account, set credentials, receive a browser session, and see an optional household welcome onboarding step.

Constraints: local-first (copy-link v1, no SMTP), AGPL core, bi-state JWT RBAC unchanged, Linear `blocks` edge from LUM-51 (per-user credentials) acknowledged but not blocking this credential-independent slice.

## Decision

1. **`user_invites` table** (migration **`044-user-invites.sql`**) — hashed opaque tokens mirroring `mcp_tokens`: CSPRNG `linv_` prefix, 16-char lookup prefix + SHA-256 hash, `hmac.compare_digest` verify; 48h TTL (`LUMOGIS_INVITE_TTL_HOURS` override); single-use via conditional `UPDATE … WHERE used_at IS NULL`.
2. **Admin routes** on `admin_users.py` — `POST/GET/DELETE /api/v1/admin/users/invites` (RBAC + `require_same_origin` on mutators); plaintext token returned once at mint; `build_invite_url` uses `LUMOGIS_PUBLIC_ORIGIN` when set.
3. **Public routes** (`routes/invites.py`) — `GET /api/v1/invites/{token}` peek (no `role` in payload), `POST …/redeem` JIT user insert + route-owned session mint; auth bypass + separate in-process rate limiters (`rate_limit.py`); peek 30/60s per IP, redeem failure buckets isolated from login.
4. **Redeem ordering** — validate email/password and argon2 **before** `transaction()`; inside txn: duplicate-email check → conditional consume → inline `users` insert (not `create_user()`).
5. **Web** — public `/invite` inside `AuthProvider` but outside `RequireAuth`; `adoptSession` on redeem; `sessionStorage` hint drives optional 5-step onboarding (household welcome first); v1 admin UI exposes role only (`allows_shared` column defaults `TRUE`, toggle hidden).
6. **Scope enforcement** — **metadata + onboarding copy only** in v1; `visible_filter` unchanged. True per-user shared-scope gating is a follow-up child issue.

## Alternatives considered

- **Stateless signed JWT invite** — rejected (revocation, single-use still needs store).
- **Pre-create disabled `pending_invite` users** — rejected for v1 (pollutes `users`, invasive to LUM-334 invariants).
- **Email delivery v1** — deferred (no SMTP in stack).
- **Expose `allows_shared` toggle in admin v1** — rejected (privacy-expectation trap without enforcement).

## Consequences

**Easier:** self-service household growth without admin-set passwords; atomic single-use at DB; revocation before use; reuses LUM-165 onboarding seam; no new Docker services.

**Harder / must-know:** new public auth-bypass surface (rate-limited); invite URLs carry secrets (`Referrer-Policy: no-referrer` + URL scrub on redemption page); `PostgresStore` single-connection / non-reentrant `transaction()` — same operational model as login (LUM-358 class); `allows_shared` is not enforced until follow-up; LUM-148 policy wizard plugs into onboarding framework after LUM-186 lands.

## Status history

- 2026-07-05: Draft created by `/explore --headless` (LUM-186).
- 2026-07-05: Revised during `/review-plan --arbitrate` R1 — v1 metadata-only `allows_shared`; inline insert + route-owned session mint; public peek omits `role`.
- 2026-07-05: Finalised by `/verify-plan --headless` — implementation confirmed.
- 2026-07-07: **LUM-577** shipped — admin invite modal exposes **`allows_shared`** toggle (forced on for admin role); redeemed users inherit the stamped value; **`users.allows_shared`** gates shared-scope reads — see **`orchestrator/services/users.py`** and **`clients/lumogis-web/tests/e2e/admin_users.spec.ts`**.
