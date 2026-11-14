# ADR-228: Transient OAuth token-refresh failures — honour Retry-After by not calling, retry only what never left
**Status:** Finalised
**Created:** 2026-11-06
**Last updated:** 2026-11-07
**Decided by:** `/explore --headless LUM-761` (claude-opus-5-5); revised by `/review-plan --arbitrate` R1 and R2 (claude-opus-5-5)
**Finalised by:** /verify-plan 2026-11-07 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-761-oauth-refresh-transient-retry.plan.md`
**Exploration:** `.cursor/explorations/LUM-761-oauth-refresh-transient-retry.md`
**Draft mirror:** `.cursor/adrs/LUM-761-oauth-refresh-transient-retry.md`
**Linear:** [LUM-761](https://linear.app/lumogis/issue/LUM-761/retry-transient-oauth-token-refresh-failures-in-place-honouring-retry)
**Relates:** ADR 196 (OAuth refresh failure kinds, which deferred this), ADR 201 (claim, not queue: send failures dead-letter on first attempt), ADR 195 (mailbox-health taxonomy), ADR 190 (OAuth2 provider connect)

## Context

`email_oauth.get_access_token` makes one POST to the provider token endpoint when the cached access token is near expiry. A 429, a 5xx or a dropped connection fails the caller at once with `OAuthRefreshError(kind="network")`. ADR 196 made that failure honest about being transient and deliberately deferred acting on it, because the poll would retry on its next tick anyway. That reasoning does not cover the callers that are not the poll. The same function serves request-thread routes (body fetch, attachments, SMTP opt-in) and the synchronous SMTP send. It also does nothing to stop a hard-down endpoint being called by every caller that finds the token expired.

Four constraints shaped the option space:

- The refresh runs under a per-mailbox lock that exists to stop concurrent refreshes.
- Replaying a refresh POST whose response was lost may be unsafe on authorization servers that detect refresh-token reuse (RFC 9700).
- Microsoft's client guidance forbids token acquisition before `Retry-After` elapses and requires ≥ 5 s before retrying a 5xx.
- Every one of roughly 20 callers inherits whatever latency the function adds.

## Decision

Add a **per-mailbox negative cache** and **one in-place retry for failures that provably never sent the request**, both inside `orchestrator/services/email_oauth.py`.

**The cache:**
- *Written:* after any `network`-kind failure that came from the network, not from a bug in Lumogis. The key is the refresh lock key plus a digest of the refresh token, so there is one entry per mailbox, shared by IMAP and SMTP. How long it lasts depends on who failed:
  - **The token server answered with `Retry-After`** (429/5xx): the header (seconds or HTTP-date, measured from the response's `Date` header when present), clamped to 5–600 s.
  - **Anything else** (a 429/5xx without the header, a transport exception, or a 2xx that is not a JSON object, such as a captive portal): 10 s. That is long enough to collapse lock waiters and a burst of callers onto one failure and to satisfy Microsoft's ≥ 5 s rule, and short enough that a one-off 503 or a blip cannot become a minute of dead-lettered sends. The precedents disagree: MSAL Python's `ThrottledHttpClient` defaults to 5 s (cap 3600 s), MSAL.NET holds a header-less 5xx for 1 minute in public-client flows. 10 s is a judgement between them, chosen for the send path.
- *Checked:* once, inside the lock, after the provider and `client_id` checks (so a configuration error still reports `config`). A valid cached access token is never blocked.
- *While an entry is live:* the same `OAuthRefreshError(kind="network")` is raised without a POST. **Honouring `Retry-After` means not calling before it elapses; it never means sleeping through it.**

**The in-place retry:** made once, after a 0.5–1.0 s jittered pause, inside the lock and within an explicit monotonic deadline. Only a fast `httpx.ConnectError` (the request never reached the server) qualifies.

**Timeouts:** the refresh POST's `httpx.Timeout(15)` is split into connect 5 / write 5 / read 15 / pool 5 s. Read keeps today's value, because nothing measured supports shortening it. One attempt's nominal worst case is 25 s, and the 30 s deadline decides only whether the retry is admitted. It is not a wall-clock ceiling: per-I/O-operation timeouts, an unbounded `getaddrinfo`, per-address connect attempts and a proxy hop can all exceed it, as they can today.

**Also fixed in the same branch:** a 2xx whose JSON is not an object is `network`, not an untyped `AttributeError`. A non-httpx exception from the POST stays `network` as today, but it is not cached and is logged at ERROR by class name.

**Never retried in place:**
- 429/5xx (the server answered; the cache handles these).
- Read-phase failures, a `ConnectTimeout` that consumed its budget, and non-JSON 2xx responses (ambiguous: the server may have processed the request).
- `credential`, `config` and `egress_blocked` failures. These are neither retried nor cached.

## Alternatives Considered

- **Park.** Defensible given no live OAuth mailbox, but it leaves the "don't call before Retry-After" gap.
- **Cache only.** A strict subset. The fallback if the retry half is judged not worth it.
- **Sleep through Retry-After / exponential backoff in place.** This is the ticket's literal approach. It puts ≥ 5 s waits on request threads, lock waiters and the shared scheduler pool, and a capped sleep is non-compliant anyway.
- **Caller-specific budgets threaded through `load_connection`.** Cross-cutting plumbing across ~20 callers for a wait the next poll tick already provides.
- **`httpx.HTTPTransport(retries=1)`.** It retries `ConnectTimeout` too, doubling the connect budget, and it hides the property in a dependency. It is a possible mechanism only if the plan verifies the tethered-egress interaction.
- **tenacity / stamina.** A new dependency for two attempts, and it pulls the design towards sleeping.
- **MSAL Python.** Microsoft-only, so it would create a second token path beside the provider-agnostic one.

Full detail: `.cursor/explorations/LUM-761-oauth-refresh-transient-retry.md`.

## Consequences

**Easier**

- A hard-down or throttling token endpoint is called at most once per mailbox per TTL, not once per caller. This is the *pattern* of Microsoft's client libraries (MSAL `ThrottledHttpClient` returns the cached error until the delay passes). The numbers differ: Lumogis uses a 10 s default and a 600 s cap, against MSAL Python's 5 s and 3600 s.
- A connect-time blip during the hourly refresh no longer fails a user's send or body read.
- Replay safety holds for **any** provider by construction. Only never-sent requests are retried in place, so a future reuse-detecting provider (LUM-689, LUM-692) needs no per-provider table.
- Mailbox health, the SMTP error code and proposal disposal are unchanged: the same type and kind are raised (ADR 195, 196, 201). `test_the_send_failure_stays_permanent_for_every_kind` is untouched.

**Harder**

- `get_access_token` gains process-local state beside the lock. It relies on the single-process invariant (LUM-832), as the lock already does.
- After a throttle or 5xx clears, a user retrying a send gets the cached failure for up to its TTL (10 s without `Retry-After`, including after a transport failure; ≤ 600 s with one). The message names the last failure and the seconds left. Reconnecting the mailbox bypasses either, because a new refresh token gives a new key.
- A grant revoked while an entry is live reports `network` instead of `credential` until the entry expires.
- The numbers (jitter, deadline, TTL, cap) are judgement calls until LUM-688 produces live data.

**Future chunks must know**

- **Do not add sleeping retries to `get_access_token`.** Every caller, including request threads, inherits them. A long wait belongs to the caller's schedule (Option 5 is the escalation path).
- **Do not retry an ambiguous refresh in place.** Microsoft tolerates the replay (old refresh tokens are not revoked on redeem), but RFC 9700 reuse detection elsewhere does not.
- **The overall deadline must be explicit.** `httpx.Timeout(x)` bounds each phase, not the call (the LUM-834 finding).
- The cache raises the identical type and kind. A new health code or a distinct "deferred" kind belongs in ADR 195 first.
- **Only a server's `Retry-After` earns a long hold.** Do not raise the 10 s default without measured evidence: on the send path every second of hold is a second of dead-lettered sends (ADR 201). The poll gains nothing from a longer hold, because `poll_interval` is floored at 60 s.
- Only the `Retry-After` header is honoured; a body-carried delay (Google's `retryDelay`) is not parsed.
- The lock's justification is a lost rotation write and duplicate POSTs. It is **not** "bricking" under Microsoft rotation, which Microsoft's docs contradict.

## Revisit conditions

- **LUM-688's live run** shows `Retry-After` values above the cap, or refresh outages longer than the TTL that cause tick-after-tick failures → revisit the cap, the TTL and (only then) caller-specific budgets.
- **A provider that detects refresh-token reuse ships** → confirm that the ambiguous class is still never retried in place and that the next-tick replay is acceptable for that provider.
- **Core goes multi-process** (LUM-832 reversed) → the cache and the lock both move out of process together.
- **Banner flapping on transient `network` refresh failures is observed** → revisit ADR 196's "suppress until N consecutive failures", which is a separate UX decision.

## Status history

- 2026-11-06: Draft created by /explore (headless). Impact scan: all 🟢, no block edges.
- 2026-11-06: Revised during /review-plan --arbitrate R1. The cache now has two TTL classes: server answers follow `Retry-After` or 60 s, and failures with no token-server answer get 10 s. Critique R1 showed that MSAL's precedent covers server answers only, and that a flat 60 s after a transport blip dead-letters every send for a minute (ADR 201). The cache check moved inside the lock, after the configuration checks. Recorded the timeout split (read kept at 15 s), the 30 s retry-admission deadline, the non-object JSON fix and the uncached unexpected-exception branch. "Sub-second" pause corrected to 0.5–1.0 s.
- 2026-11-07: Revised during /review-plan --arbitrate R2. The default TTL for a 429/5xx without `Retry-After` drops from 60 s to 10 s, the same as the transport case, so there is one default and only a server's header earns a long hold. Critique R2 showed the "MSAL `ThrottledHttpClient`" attribution was wrong on the numbers: MSAL Python defaults to 5 s with a 3600 s cap, and the 1-minute figure is MSAL.NET's, for 5xx in public-client flows only. A 60 s hold on the commonest real failure (a one-off 503) re-created the minute of dead-lettered sends that R1 removed for transport failures. The Consequences no longer claim the numbers match Microsoft's libraries.
- 2026-11-07: Finalised by /verify-plan — implementation confirmed decision. `orchestrator/services/email_oauth.py` (`_post_refresh`, the `_REFRESH_BACKOFF` cache, `_parse_retry_after`) matches the Decision as revised in R2; 51 tests in `orchestrator/tests/test_oauth_refresh_retry.py`, and all 17 planned mutations fail their named test.
