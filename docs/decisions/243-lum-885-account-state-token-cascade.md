# ADR-243: Account-state changes cascade to every credential; access and refresh JWTs kept apart
**Status:** Finalised
**Created:** 2026-11-08
**Last updated:** 2026-11-08
**Decided by:** claude-opus-5-5 (/explore --headless LUM-885), revised at /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-08 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-885-account-state-token-cascade.plan.md`
**Exploration:** `.cursor/explorations/LUM-885-account-state-token-cascade.md`
**Draft mirror:** `.cursor/adrs/LUM-885-account-state-token-cascade.md`
**Linear:** LUM-885 (child of LUM-883, security audit #2)

## Context

Security audit #2 found that only `services/users.py:set_disabled` revoked everything a user holds. The other account-state changes each left one credential alive:

- **Delete** left `lmcp_` MCP tokens live. `mcp_tokens.verify` never checked that the owner exists, and ADR 017's "revoke, then retain rows" intent had been lost.
- **Demotion** left the admin access JWT valid for its TTL, long enough to re-promote oneself.
- **Password change** (all four paths share `apply_new_password_in_transaction`) and **logout-all** left MCP tokens live.
- Unredeemed invites minted by an admin survived that admin's demotion, disable or delete, and could still create an admin account.

Separately, `auth.py:_refresh_secret` fell back to `AUTH_SECRET`, and no JWT carried a `typ`. Where the two secrets matched, a refresh JWT verified as a 30-day access Bearer that ignored `token_version` revocation. The knowledge-graph service (`services/lumogis-graph/auth.py`, AGPL since ADR 229) verifies Core access JWTs under `AUTH_SECRET` too.

Constraints: no new services or migrations; reuse the ADR 017 and ADR 041 cascade pattern. Default compose installs must not see a forced sign-out or a refused boot at upgrade.

## Decision

1. **One cascade helper.** All account-state changes revoke credentials through one helper pair in `services/users.py`, extracted from `set_disabled`: `revoke_credentials_in_transaction` (inside the caller's transaction) and `finish_credential_revocation` (after the commit; never raises). `revoke_all_credentials` wraps both for logout-all.
   - In one transaction the helper bumps `token_version` **first** (so the `users` row lock is held before anything else is revoked, which a later fix of the in-flight refresh race relies on), then revokes MCP tokens (optional), revokes sessions (optional) and, on delete, disable and a real demotion, revokes the user's unredeemed invites. After the commit it clears the sid, `tv` and permission caches (each step logged and swallowed on failure) and emits the audits, attributed to the acting admin when there is one.
   - `set_disabled`, `delete_user` (revoke before the row delete; token and invite rows retained), a real role demotion, every password change and logout-all all go through it. Promotion and any other role change bump `token_version` only; a same-role change does nothing.
   - **MCP tokens are always revoked**, and the Web password forms and the admin demote and delete confirmations state this, with the active MCP-token count where known.
   - A structural test (`test_account_state_single_door.py`) fails any `token_version + 1` or `UPDATE users SET role / disabled = TRUE` outside the owning modules.
2. **Owner check at use.** `mcp_tokens.verify` left-joins `users` and rejects missing or disabled owners; a missing owner is accepted only in single-user mode (`AUTH_ENABLED=false`) for the synthetic `default` user, which has no `users` row. Invite peek and redeem accept an invite only while its creator exists, is not disabled and is still an admin, re-checked inside the consume `UPDATE`. Both are backstops for any path that forgets the cascade, and for credentials orphaned before this fix.
3. **`typ` claim.**
   - Access JWTs carry the payload claim `typ: "access"` and refresh JWTs carry `typ: "refresh"`.
   - Every access verifier (orchestrator `verify_token`, KG `_verify_token`) **requires** `typ == "access"`. A typ-less access token in flight gets one 401, which the Web client's 401-refresh absorbs.
   - `verify_refresh_token` rejects `typ == "access"` and tolerates a missing `typ`, because refresh JWTs are bound to their `auth_sessions` row by hash. A code note marks the tolerance for removal after one refresh TTL.
4. **Distinct refresh secret.**
   - `_refresh_secret` no longer falls back to `AUTH_SECRET`.
   - With `AUTH_ENABLED=true`, `main._enforce_auth_consistency` refuses to boot when the refresh secret is missing, a placeholder, or equal to `AUTH_SECRET`. The message names the variable the value came from and never contains a secret.
   - `docker-entrypoint.sh` self-heals the compose case: it writes a generated `JWT_REFRESH_SECRET` to `.env` when auth is on and the key is missing or a placeholder, and exports the `.env` value for the current run (also on a restart that kept a stale environment). It follows Python's precedence exactly (a non-empty `LUMOGIS_JWT_REFRESH_SECRET` wins and is never overridden), never exports a value it failed to persist, and leaves `.env` forms it does not parse to compose and the boot gate.
   - `make doctor` reports a missing, placeholder or shared refresh secret with auth on, before an upgrade restart.
   - LibreChat keeps sharing `JWT_REFRESH_SECRET` by default; `LUMOGIS_JWT_REFRESH_SECRET` separates the two.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-885-account-state-token-cascade.md`.

- **Revoke-MCP checkbox on self password change and logout-all:** not chosen as the default, because the recovery paths revoke regardless and the checkbox adds route and OpenAPI surface for routine-rotation convenience. The helper's `revoke_mcp` flag keeps it available later.
- **Per-site patches copying `set_disabled`:** rejected, because a missed copy is exactly how the original gap happened.
- **DB role re-read in `require_admin`:** rejected as the primary fix. It covers only `require_admin` routes, and the `tv` bump already ends the stale JWT everywhere `tv` is checked.
- **Flip `LUMOGIS_REQUIRE_TV_CLAIM` instead of adding `typ`:** rejected. It identifies the token kind only indirectly, does not reach the KG verifier, and is separate later work.
- **Shape-based back-compat (typ-less and no `jti` counts as access):** rejected. It leaves a permanent heuristic in the hot path to cover a 900 s window that the 401-refresh already absorbs.
- **Opaque access tokens, asymmetric signing, or an `ON DELETE CASCADE` foreign key:** rejected. These are too invasive (ADR 041), add key management, or destroy the forensic token rows that ADR 017 keeps.
- **A minimum length for the refresh secret:** rejected as a non-goal. `AUTH_SECRET` has no length rule either; the compose default is 64 hex characters.

## Consequences

- **Easier:** account-state changes have one revocation path to audit and test. Delete is no longer weaker than disable. A demoted, disabled or deleted admin's pending invites can no longer create an account. A refresh cookie can no longer act as a Bearer under any secret configuration. The KG service rejects refresh tokens too.
- **Harder:**
  - After a password change, a sign-out-everywhere, a demotion or a delete, MCP clients (Claude Desktop, the `lumogis-mcp` stdio bridge, Cursor) need a newly minted token.
  - Hand-written `.env` files with `AUTH_ENABLED=true` and no separate refresh secret stop booting until one is set, with a remediation message. If the refresh secret changes on such an install, every member signs in again once.
  - In multi-user mode, an MCP token still owned by the single-user `default` account is rejected.
  - Tests that hand-craft access JWTs must include `typ`.
  - Invites sent by an admin who steps down are cancelled; another admin re-sends them.
  - A refresh rotation already in flight when a password change or logout-all commits can still leave one session (deferred; the helper's `tv`-first order keeps that fix local to `rotate_refresh`).
  - An old-`tv` access JWT stops working once the in-process `tv` / sid caches drop their entry, at most their TTL (30 s by default) after the commit; for the command-line password reset, which runs in another process, that TTL is the only bound.
- **Future work must know:** any new JWT kind gets its own `typ` value and verifier, and must never be accepted by `verify_token`. Any new credential kind a user can hold must join the helper and get an owner check at use. `mcp_tokens.verify` depends on `users`, so a future split of MCP tokens into another store must carry the owner check. The KG service still ignores `tv`, so a demoted admin keeps KG admin routes for at most the access TTL. The helper's steps join the caller's transaction by thread affinity (`config.get_metadata_store()`); a change to that affinity must re-run the live atomicity tests in `tests/integration/test_account_state_cascade_live.py`.

## Revisit conditions

- MCP-client users report that routine password rotation breaking MCP clients is a real burden. → Add the `revoke_mcp_tokens` choice on the self-service paths.
- MCP tools start reading `role` or offering admin-only operations. → Revisit whether demotion revoking MCP tokens is still the right default, and replace the synthetic `UserContext(role="admin")` on `/mcp`.
- A non-Web client keeps an access token across restarts without 401-refresh handling (e.g. a future Lumogis Desktop). → Re-check the strict access-`typ` upgrade cost before the next such migration.
- One refresh TTL (default 30 days) after the release that ships this. → Drop the missing-`typ` tolerance in `verify_refresh_token`.
- `LUMOGIS_REQUIRE_TV_CLAIM` becomes on by default, or the access TTL is clamped. → Re-check whether the KG service needs its own `tv` check, or whether the TTL bound is enough.

## Status history
- 2026-11-08: Draft created by /explore (headless, claude-opus-5-5)
- 2026-11-08: Revised during /review-plan --arbitrate R1 — added unredeemed invites to the cascade and a creator check at redeem; single-user `default` exception for the MCP owner check; helper bumps `tv` first; entrypoint mirrors Python's refresh-secret precedence; doctor check. No earlier decision overturned.
- 2026-11-08: Finalised by /verify-plan — implementation confirmed decision. Numbered 243 because 241 and 242 are claimed by the unmerged `agent/lum-877` and `agent/lum-886`; `/merge-workflow` re-checks numbering at merge.
