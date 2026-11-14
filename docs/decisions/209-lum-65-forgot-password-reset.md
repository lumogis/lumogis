# ADR 209: Forgot-password — phased recovery (copy, then email + operator SMTP + ALTCHA)
**Status:** Finalised
**Created:** 2026-08-08
**Last updated:** 2026-08-09
**Decided by:** /explore LUM-65 (Grok 4.5); revised /review-plan --arbitrate R1, R2 (Opus 5)
**Finalised by:** /verify-plan 2026-08-09 (Opus 5), after the P0 remediation proved the mechanism
**Plan:** `.cursor/plans/LUM-65-forgot-password-reset.plan.md`
**Exploration:** `.cursor/explorations/LUM-65-forgot-password-reset.md`
**Draft mirror:** `.cursor/adrs/LUM-65-forgot-password-reset.md`
**Linear:** [LUM-65](https://linear.app/lumogis/issue/LUM-65/self-hosted-forgot-password-emailsms-reset-flow)

## Context

ADR 029 shipped self-service, admin, and CLI password mutation and deferred email/SMS reset. The Lumogis Web login page still offered no recovery path or guidance, so a forgotten password was a hard stop for members and a shell-only path for a sole admin. LUM-65 asked for email/SMS reset; the owner locked: **phased** (login guidance first, email second), **operator-proven SMTP**, recover **both** member and sole-admin cases, treat email reset as an **internet-box** feature with **CAPTCHA**, and **SMS out**.

**Why this was held at Draft for a day.** The R2 decision below rests on the reset token and its mail being committed *together*. A post-implementation review found that premise did not hold: `config.get_metadata_store()` returns one process-wide connection and `PostgresStore.transaction()` toggles that connection's `autocommit`, so the "same transaction" the decision names could not be opened safely from a sync route or a scheduler thread. Finalising then would have recorded a mechanism the code did not have. It was finalised only once the mechanism was built and proved — see the amendment under **Decision**.

## Decision

Ship password recovery in **two passes**:

1. **Pass 1 — Guidance only.** On the login screen (and operator docs), state that household members should ask an admin (**Admin → Users** password reset), and that operators with shell access use `python -m scripts.reset_password` from `orchestrator/`. No tokens, no SMTP.

2. **Pass 2 — Email magic-link reset.** Operator configures **transactional SMTP in Admin UI** (**Admin → System mail**), stores it as system-tier connector `system_smtp` (Fernet), and must pass a **verified Send test** before email reset activates. Self-hosted **ALTCHA** (MIT) on the request path whenever email reset is enabled. Public, enumeration-safe request/redeem with invite-shaped hashed tokens; links from **`LUMOGIS_PUBLIC_ORIGIN` only**. Successful redeem uses `_apply_new_password`. **Single writer** for `system_smtp` — System mail routes only; the connector is rejected on **all four** generic credential routers (per-user, admin-per-user, household, system) and hidden from the registry listing, because registering it opens every tier and `resolve_runtime_credential` prefers the **user** tier. Reset mail is written to a **durable outbox in the same transaction as the token** and sent by a background drain job; **no SMTP runs on the request path** (R2 — a synchronous send both reopens the enumeration timing oracle and lets an unauthenticated caller hold a shared threadpool worker for 30s). Outstanding reset tokens are invalidated inside `_apply_new_password`, so an admin or CLI reset kills any live emailed link. If SMTP/origin/HMAC are unset or unproven, keep Pass-1 UX only.

**Amendment (2026-08-09) — how "the same transaction" is actually obtained.** Every atomic unit on this feature's request and scheduler paths runs on a **dedicated connection** via `PostgresStore.isolated_transaction()`, never on the process-wide `transaction()`. The shared connection cannot carry a transaction opened from a sync FastAPI route or an APScheduler job: `transaction()` toggles `autocommit` on one connection shared by ~40 AnyIO threadpool workers, so a second concurrent caller raises, and — worse — an ordinary `execute()` on another thread is silently *enrolled* in the open transaction and discarded on rollback. Both modes are reproduced against a real Postgres in `orchestrator/tests/integration/test_postgres_store_concurrency_live.py`. The decision's atomicity guarantee is therefore real, but it is delivered by connection isolation rather than by the shared-connection helper the R2 arbitration assumed. Generalising this beyond LUM-65 — a connection pool, and the ~16 remaining `ms.transaction()` call sites — is **[LUM-816](https://linear.app/lumogis/issue/LUM-816/postgresstore-shares-one-connection-process-wide-transaction-is-unsafe)**, not this ADR.

**Amendment (2026-08-09, second round) — a caught statement error is not a handled one.** Postgres aborts the **entire** transaction on any statement error: every later statement fails with `InFailedSqlTransaction`, and `COMMIT` silently degrades to `ROLLBACK`. A `try/except` around a statement therefore does not make it optional — it produces a transaction that can no longer do anything while appearing handled, and reports success. Two call sites on this feature were written on the opposite assumption, and the first of them **broke the CLI lockout-recovery path it was added to protect**. The only construct that makes a statement genuinely optional is `SAVEPOINT` / `ROLLBACK TO SAVEPOINT`, now available as `PostgresStore.savepoint()` and used by `password_reset.invalidate_for_user` and `mint()`. Proved in `orchestrator/tests/integration/test_password_reset_savepoint_live.py`, whose first test asserts the hazard itself.

**Amendment (2026-08-09) — redeem is not gated on enablement.** `POST /password-reset/redeem` requires only `auth_enabled()`. Three of `email_reset_enabled()`'s four conjuncts (public origin, ALTCHA HMAC, passing Send test) are preconditions for *minting and sending* a link, not for redeeming one already delivered. Gating redeem on all four meant routine relay maintenance silently invalidated every outstanding link.

**SMS is out of product scope for this ADR.** Cloud CAPTCHAs are rejected. Bundling an MTA in Lumogis Compose is rejected. Household mailbox SMTP is not the system mailer.

## Alternatives Considered

- **Copy-only forever** — insufficient for sole-admin without shell; kept only as Pass 1.
- **Env-only SMTP (no Admin UI)** — rejected after owner lock; Admin UI + Send test is the configuration surface (env was the exploration's first sketch).
- **Household mailbox SMTP as system mailer** — circular lockout; rejected.
- **Bundled mailcow/Postfix** — disproportionate; rejected.
- **Cloud CAPTCHA** — phones home; rejected.
- **Shared-connection `transaction()` for the token+outbox unit** — assumed by the R2 arbitration, disproved at implementation. Superseded by `isolated_transaction()`; see the amendment above.

## Consequences

**Easier:** Immediate UX honesty on login; sole-admin recovery once SMTP is proven; reuse of invite tokens, `_apply_new_password`, the `outbound_http_url` SSRF gate, system credential store; shared mailer **and shared outbox** for LUM-264.

**Harder:** Operators must configure Admin System mail + HMAC + public origin; internet exposure requires Altcha + rate limits; one more table and one more scheduler job; reset mail arrives up to a drain interval (~15s) after the request, which operators must not read as failure.

**Operator-visible change beyond this feature:** both SMTP transports now verify TLS certificates and hostnames (`ssl.create_default_context()`). A relay with a self-signed certificate that previously connected silently will now fail. That is the correct direction for a credential-bearing channel, but it is a behaviour change on the pre-existing mailbox sender, not only on the new one.

**Future chunks must know:** **A `try/except` around a SQL statement does not make it optional — use `SAVEPOINT`.** This cost two defects on this feature alone, and the unit fakes cannot catch it because they raise plain Python exceptions, which abort nothing. System mail ≠ `email_smtp_sender`; never trust `Host` for reset links; only System mail routes may write `system_smtp`, and the block covers **all four** credential routers; `system_mail` must read the system tier directly rather than via `resolve_runtime_credential`; the outbox `body` is secret-bearing (plaintext reset URL) so rows are deleted on send, not retained. **Any new atomic unit on a request or scheduler path must use `isolated_transaction()`, not `transaction()`, until LUM-816 lands.** The outbox retry ladder is bounded by the token TTL, not by its rung count — a longer ladder would deliver links that are already dead.

## Revisit conditions

- ALTCHA blocked → Cap.js in-process. (Note the v1/v2 branch hazard: `altcha` is pinned to `2.1.0` / v2 with the npm widget on `^3`; a major bump on either side is a wire-contract change, not a routine upgrade. The v2 `cost` parameter is a per-candidate KDF iteration count — total work is `cost × ~256` — so it is not comparable to a v1 `max_number`.)
- ~~Sync SMTP on request proves too slow → durable outbox + worker~~ — **consumed at R2**, before implementation: the outbox is now the decision, not the fallback.
- Outbox drain latency (~15s) proves too slow for an operator → shorten the interval or add an opportunistic post-commit kick; do **not** move the send back onto the request path.
- **LUM-816 lands a connection pool** → `isolated_transaction()` and `transaction()` converge; revisit this ADR's amendment and retire the distinction rather than leaving two names for one thing.
- **ALTCHA solve times are reported as slow on real phones** → re-measure and retune `LUMOGIS_ALTCHA_COST` (currently 200, ~1.4s median on an idle desktop). Do not raise it without measuring the tail: the solve is a geometric search, so ~2% of attempts take 4× the mean.
- Backup recovery email required → additive `users` column.
- SMS mandated → new ADR.

## Status history

- 2026-08-08: Draft created by /explore LUM-65.
- 2026-08-08: Revised during /review-plan --arbitrate R1 — Admin UI + Send-test is normative (not env-first); sync send; single writer for `system_smtp`; ALTCHA + RequestRateLimiter contracts.
- 2026-08-08: Revised during /review-plan --arbitrate R2 (first critiques by the real Fable 5 / Sonnet 5 — R1's were stand-ins) — **sync request-path send replaced by a durable outbox + drain job** (timing oracle, threadpool starvation, and mint-without-mail all resolved together; revisit condition consumed pre-implementation); `system_smtp` blocked on all four credential routers and read only from the system tier; reset tokens invalidated inside `_apply_new_password` for admin/CLI/self-service changes; ALTCHA pinned to the v2 branch with `result.verified` and single-use signatures.
- 2026-08-08: **Held at Draft** by /verify-plan — four P0 defects, two of which showed the R2 atomicity premise did not hold on the shared `PostgresStore` connection.
- 2026-08-09: **Reopened after finalisation.** A post-closure security review (adversarial second pass) and a high-effort code review found 13 further defects, three of them introduced by the remediation itself: a resolver differential that let a hostname's AAAA record bypass the SSRF guard, a `try/except` that could not un-abort a Postgres transaction (breaking CLI lockout recovery), and an expiry check on the wrong branch. All closed; amendments recorded above. The lesson is the one this ticket keeps teaching at successive layers: **the fakes model SQL faithfully and transaction semantics not at all**, so each round's green suite was structurally incapable of seeing that round's defect.
- 2026-08-09: **Finalised** — P0s, P1s and P2s closed. Atomicity amended to `isolated_transaction()` on a dedicated connection, proved against a real Postgres; redeem de-gated from `email_reset_enabled()`; generalisation filed as LUM-816.
