# ADR-252: Refresh rotation re-checks the session and token version inside its transaction (LUM-896)
**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none. Shipped as a targeted `exec:claude` session without a plan / verify cycle
**Exploration:** `.cursor/explorations/refresh_rotation_revoke_race_retro.md`
**Draft mirror:** `.cursor/adrs/refresh_rotation_revoke_race.md`
**Linear:** LUM-896 (security audit #2 ID-008, deferred from LUM-885)

## Context

[ADR 243](243-lum-885-account-state-token-cascade.md) (LUM-885) promises that a password change and "log out everywhere" revoke every session, through `revoke_credentials_in_transaction` in `services/users.py`. ADR 243 recorded one exception and deferred it: a refresh rotation already in flight. Its helper bumps `token_version` first so that the `users` row lock is taken before anything else is revoked, which this fix relies on.

`rotate_refresh` (`orchestrator/services/auth_sessions.py`) read the session row and `token_version` outside any transaction. It then opened a transaction that inserted the new session and revoked the old one with `… WHERE id = %s AND revoked_at IS NULL`, and never checked the row count. Two orders left a live session after the revoke:

1. The revoke commits between the rotation's read and its transaction. The old-row `UPDATE` matches nothing, and the `INSERT` still creates a live session.
2. Under READ COMMITTED, the revoke runs while the rotation transaction is open. It cannot see the uncommitted `INSERT` and leaves that row alive.

The first access token carries the old `tv` and is rejected, but the Web client's 401 → refresh retry then succeeds, because the refresh route checks only `disabled`. Disable and delete were not affected: the route re-reads the user. The weekly code review (range `7e100140..3395a2d2`) raised it as LUM-896.

## Decision

Everything that decides "this refresh may still rotate" is re-checked inside the rotation transaction, in this order:

1. `SELECT token_version FROM users WHERE id = %s FOR SHARE`. No row raises `RefreshError`.
2. `UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL RETURNING id`. No row raises `RefreshError` (HTTP 401; the transaction rolls back).
3. `INSERT` the new session.

The pre-transaction read stays. It still does unknown-row, hash, expiry and reuse detection on a row that was already revoked when it was read.

- **Lock order is `users` then `auth_sessions`**, the same order as the revoke helper (D9 in ADR 243). With the session row locked first, a concurrent revoke would hold `users` and wait on the session row while the rotation held the session row and waited on `users`, which is a deadlock. The issue's Done-when listed the conditional `UPDATE` first; it was put second for this reason. Both statements are inside the transaction and before the `INSERT`, as the Done-when requires.
- **Why the two serialise.** A revoke already holding the `users` row makes `FOR SHARE` wait; the rotation then reads the new version, and its `UPDATE` finds the row revoked. A revoke that starts after the `FOR SHARE` waits for the rotation to commit; its `UPDATE auth_sessions … WHERE user_id` is a new statement with a new snapshot, so it revokes the row the rotation inserted.
- **A lost race is `RefreshError`, not reuse.** The audit's ID-008 fix design treats "no row" as reuse (revoke the family). That was not taken: the loser of a race against a password change would log a false `reuse_detected`, and two tabs refreshing one cookie at the same instant would lose the whole family. Two simultaneous presentations of one cookie now mint one child, not two. The loser gets a 401, and the route clears its cookie.

As built: `orchestrator/services/auth_sessions.py` (`rotate_refresh`), unit test `test_rotate_refresh_fails_when_the_row_was_revoked_after_the_read` in `orchestrator/tests/test_auth_phase1.py`, live tests L8a/L8b in `orchestrator/tests/integration/test_account_state_cascade_live.py`, matrix row 1.6.90, and a `CHANGELOG.md` `[Unreleased]` → Security sub-bullet under LUM-885.

## Alternatives Considered

- **`SELECT … FOR UPDATE` on the session row, then the checks.** Not chosen: it takes the session lock before `users` (the deadlock above), and the conditional `UPDATE … RETURNING` does the check and the revoke in one statement.
- **Treat a lost race as reuse (ID-008's suggested fix).** Not chosen at ship time for the false positive and the two-tab cost above. See Revisit conditions.
- **SERIALIZABLE for the rotation transaction.** Not chosen: the revoke side runs at READ COMMITTED, so a serialisation failure would surface as a 500 to be retried, and the row lock gives the same ordering with no retry loop.
- **Re-check `token_version` in the refresh route after rotating.** Not chosen: it does not stop order 2, where the inserted row is invisible to the revoke.

## Consequences

- A committed password change or "log out everywhere" leaves no live session, whichever order it interleaves with a refresh. ADR 243's "except a refresh in flight" exception is closed for sessions.
- A refresh can briefly wait behind a password change or "log out everywhere" (row lock on `users`). Both are short transactions.
- Any new path that revokes sessions **and** locks `users` must lock `users` first, or it can deadlock with `rotate_refresh`. A path that only revokes sessions (logout, revoke-one, reuse handling) takes no `users` lock and is safe.
- Two tabs refreshing one cookie at the same instant: one now gets a 401 and its cookie cleared, where before both succeeded and forked the family. A slightly later loser already hit reuse detection, which is harsher. The Web client single-flights refresh within a tab, not across tabs.

## Revisit conditions

- A racing replay of a stolen refresh cookie must also revoke the winner's child (ID-008's "treat no row as reuse"). Decide whether the two-tab cost is acceptable, or tell the two apart (for example by device fingerprint).
- The revoke helper's order changes (anything locked before the `users` bump), or `rotate_refresh` gains a statement that locks another table.
- The metadata store's default isolation level changes from READ COMMITTED.
- The Web client adds cross-tab refresh coordination, which would remove the two-tab case.

## Linear linkage (Product OS)

- **Existing issue:** LUM-896 (https://linear.app/lumogis/issue/LUM-896/a-refresh-racing-a-password-change-or-log-out-everywhere-can-leave-a). Implemented on branch `claude/lum-896`, not yet merged.
- **New issue needed:** no.
- **Historical evidence only:** no.
- **Follow-ups:** the reuse-on-race question is a Revisit condition above and a Later item for LUM-883 (where ID-008 sits). Whether ID-008 moves to the fixed table of `docs/security/security-audit-2026-11.md` depends on that decision ([ADR 249](249-lum-883-security-audit-public-summary.md)).

## Testing retrospective

- **Added:** `test_rotate_refresh_fails_when_the_row_was_revoked_after_the_read` (unit, stale pre-read via the fake store); `test_l8a_revoke_committing_after_the_preread_wins` and `test_l8b_revoke_starting_inside_the_rotation_transaction_wins`, each parametrised over `change_own_password` and `revoke_all_credentials`, on real Postgres. L8a pauses the rotation in `mint_refresh_token` (after the pre-read, before the transaction). L8b pauses it after the `INSERT` and waits until `pg_stat_activity` shows the revoke waiting on a lock. Both assert no unrevoked session for the user afterwards and that the presented cookie (and, in L8b, the rotated-in one) cannot refresh.
- **Fail without the fix:** all five, checked by swapping in the pre-fix `auth_sessions.py` (unit: 200 instead of 401; L8a: rotation returned a session; L8b: the revoke never waited).
- **Run:** targeted pytest (173 passed), live cascade file on a throwaway `postgres:16.8` with `init.sql` + migrations (15 passed), the full orchestrator unit suite, and `make lint`. Counts are in the exploration.
- **Docs:** `docs/testing/automated-test-strategy.md` needs no change. Release skills need no change.

## Status history

- 2026-11-10: Finalised by /record-retro (retrospective).
