# ADR-197: The SMTP opt-in route answers with the credential layer's own error contract

**Status:** Accepted
**Created:** 2026-08-01
**Last updated:** 2026-08-01 (amended by LUM-771 and LUM-772 — the 200-body error vocabulary and the standalone-password refusal)
**Decided by:** `/explore LUM-759` (claude-opus-5)
**Linear:** [LUM-759](https://linear.app/lumogis/issue/LUM-759/set-email-smtp-maps-a-transient-oauth-refresh-failure-to-422-imap-not), amended by [LUM-771](https://linear.app/lumogis/issue/LUM-771/set-email-smtp-drops-the-oauth-arguments-so-sending-cannot-be-re) and [LUM-772](https://linear.app/lumogis/issue/LUM-772/p2-an-xoauth2-mailbox-can-persist-an-smtp-app-password-that-is-never)
**Relates:** ADR 196 (LUM-757 `OAuthRefreshError.kind`), ADR 195 (LUM-633 poll health)

## Context

`set_email_smtp` (`routes/signals.py:523-529`) wraps `email_credentials.load_connection` in `except Exception` and answers every failure with `422 imap_not_configured`, `from None`, and no log line.

`load_connection` documents four raise types. Only one of them means "not configured". The others are a decrypt failure, a settings/SSRF-policy rejection, and — since LUM-686 — an OAuth token-refresh failure, which may be a network blip, a revoked grant, or an operator who never set `LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID`. A fifth case, any bug in Lumogis, is also caught and reported as a client error.

The catch was written on 2026-07-23 (`0717f6c`, LUM-650). `load_connection` became a network call on 2026-07-24 (`6140e0c`, LUM-686). Nobody chose the wrong status code; the function under the `try` changed meaning one day after the catch was written.

Two constraints shape the option space:

* **The mapping is already written down.** `routes/connector_credentials.py:29` documents the domain → HTTP contract for these exact exception types, and `signals.py:606` — twelve lines below the defect — already answers "operator has no client id" with `503 oauth_unavailable`.
* **There is no compatibility cost.** `setEmailSmtp` has exactly one caller in the web client and it discards the response inside an empty `catch {}`. Changing the status codes breaks nothing — and since that catch is replaced in this same chunk, the new codes arrive with their only consumer written against them from the start, rather than inheriting a client that predates them.

## Decision

**Narrow the catch to `load_connection`'s documented raise surface, and map each case to the contract the repo already documents.** Reading `OAuthRefreshError.kind` is one branch of that, not the whole change.

| Exception | Status | `code` | Source of the mapping |
|---|---|---|---|
| `ConnectorNotConfigured` | 422 | `imap_not_configured` | unchanged — now means what it says |
| `OAuthRefreshError` `kind="network"` | 503 | `mailbox_unreachable` | RFC 9110 §15.6.4 — temporary, retry appropriate |
| `OAuthRefreshError` `kind="egress_blocked"` | 503 | `egress_blocked` | `smtp_transport.py:155` already uses this string |
| `OAuthRefreshError` `kind="config"` | 503 | `oauth_unavailable` | `signals.py:606`, identical condition |
| `OAuthRefreshError` `kind="credential"` | 422 | `imap_reauth_required` | reconnect is the user's action; LUM-701 shipped the affordance |
| `CredentialUnavailable` (other) | 503 | `credential_unavailable` | `connector_credentials.py:29` |
| `ImapHostBlockedError` (⊂ `ValueError`) | 503 | `egress_blocked` | `feed_monitor` classifies it `egress_blocked`; ADR 195 requires agreement |
| `ImapHostUnresolvedError` (⊂ `ValueError`) | 503 | `mailbox_unreachable` | transient DNS — the split LUM-633 added exactly to avoid collapsing it |
| `ValueError` (other) | 422 | `imap_settings_invalid` | bad stored settings, user-correctable |
| anything else | 500 | — | `_log.exception`; cause chain preserved |

**Corrected during planning — two rows above were wrong when this ADR was drafted.** A flat `ValueError → 422` would have collapsed `ImapHostBlockedError` and `ImapHostUnresolvedError` back together. LUM-633 split those two types precisely because a permanent policy block and a transient DNS failure were both bare `ValueError` and therefore told the user the same wrong story. Re-collapsing them here would have reproduced that defect on a second surface — the same trap as the `egress_blocked` omission that LUM-757's arbitration caught.

**And the four health codes are not sufficient to pick a status on this surface.** `config` covers both a bad stored port (the user typed it — 422) and a missing operator `LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID` (503; they cannot fix it). Mapping it to one status is wrong either way, and mapping it to 422 would tell a household member to fix an operator's environment variable — the exact harm the parent issue exists to remove. The distinguishing bit is *which exception carried the code*, so `config` is resolved at the catch site rather than in the table: from an `OAuthRefreshError` it is operator-owned, from a `ValueError` it is user-entered. This is the same ambiguity LUM-760 describes on the banner surface, reached independently here.

**Therefore there is one classifier, not a new one.** `feed_monitor._classify_construction_failure` already maps this exact raise surface to ADR 195's codes. Hand-rolling a second mapping in the route would be a **third** classifier of one surface, against ADR 195's standing "must agree" rule. The body moves to `services.email_credentials.classify_credential_failure`, `feed_monitor` delegates, and the route calls the same function — a route reaching into `signals/` for a `_`-prefixed helper would be the wrong direction.

Every failing branch logs before raising, and `from None` is dropped so the cause chain survives.

**The setup form stops discarding the result.** `EmailMailboxSetupForm.tsx:283-291` wraps `setEmailSmtp` in an empty `catch {}`, so today none of the above is visible to anyone. It renders a non-blocking inline notice keyed on `code` instead — the mailbox still connects regardless, which is the property the empty catch was protecting. Without this half, the mapping is a contract no client reads.

**`kind="credential"` gets its own code, `imap_reauth_required`, rather than reusing `imap_not_configured`.** The two are returned by the same function in the same chunk and their fixes are opposite — "add your mailbox" versus "your sign-in expired, reconnect it". Collapsing them would reproduce, inside this fix, the exact substitution the lane exists to undo. The distinct code is actionable: LUM-701 shipped a self-service reconnect, and `pollHealthMessage.ts:20` already words the `credential` health code as "Reconnect it", so this reuses that vocabulary rather than inventing a parallel one.

**Transient conditions get 5xx, not 4xx.** A 4xx asserts the client must change the request; a token endpoint that timed out is not something the client can fix by changing anything. That is the same substitution ADR 196 removed from the health banner, on a different surface.

## Amendment — LUM-771: the same contract applies to the 200-body error, and the verify must use the mailbox's own credential

The table above covers this route's `HTTPException` branches — everything that can fail *before* the connect-test. The connect-test's own failure is reported differently: `200 {ok:false, tested:true, error:<code>}`, because a refused password is an expected outcome of an explicit test rather than a malformed request. That vocabulary was outside LUM-759's scope and inherits the same rule.

**An OAuth mailbox's verify uses its access token.** `verify_smtp_connection` has accepted `auth_type`/`access_token` since LUM-693 and the OAuth device-flow route passes them; this route did not, so an xoauth2 connection — whose `password` is deliberately `None` — was connect-tested with an empty app password and could never succeed. The discriminator is `ImapConnection.auth_type`, **not** the presence of `oauth_access_token`: the dataclass states the fact directly, and a presence check silently means "app password" for any future path that builds the connection before minting a token.

**A failing OAuth verify answers `smtp_oauth_denied`, not `smtp_auth`.**

| Condition | 200 body |
|---|---|
| xoauth2, provider or tenant refuses submission | `error: "smtp_oauth_denied"` |
| xoauth2, client asks to set a standalone sending password | **`422 smtp_password_not_applicable`** — not a 200; the request is refused before any verify |
| app password, wrong password | `error: "smtp_auth"` — unchanged |
| either, server unreachable | `error: "smtp_connect"` — unchanged |
| either, egress-blocked | `{ok:true, enabled:true, tested:false}`, **`error: null`** — the soft-save |

Same reasoning as `imap_reauth_required` above: two conditions whose fixes are opposite must not share a code. An OAuth mailbox has **no password to correct** — the fix is a permission at the provider (a Microsoft 365 tenant with SMTP AUTH off, a missing send scope, a revoked grant) — so a client keying on `smtp_auth` would offer a password field to a user who cannot use one. The narrowing is deliberately confined to `smtp_auth`: remapping `smtp_connect` too would tell a user their provider forbids sending when the server was merely unreachable.

**An OAuth mailbox refuses a standalone sending password (LUM-772).** `reuse_imap_password: false` on an xoauth2 mailbox answers **`422 smtp_password_not_applicable`**. `load_smtp_connection` returns early with the OAuth token for such a mailbox and ignores any stored `app_password`, so accepting one persisted an encrypted secret that nothing would ever read and no revocation flow would ever clear. **Refused rather than silently dropped**, on this ADR's own reasoning: the route's whole error contract exists because it reported the wrong cause, and discarding a password the user typed without telling them is that failure in a new place. The code is distinct from `app_password_required`, which asserts the opposite — that one is owed.

**`egress_blocked` is not in this vocabulary and must not be added.** `verify_smtp_connection` returns `(True, False, None)` for a block — the code is `None`, and that is exactly what makes it a soft-save rather than a failure. A branch for it here can never execute.

**`GET /sources` publishes `smtp_host_required` so the 422 above is avoidable.** `smtp_host_required` is the one code in the table a client can predict: it is returned precisely when the mailbox's IMAP host has no provider-registry entry. The flag is **derived on read** from `sources.url` by the same `provider_for_host` this route matches with — one matcher, nothing to drift — and gated on `source_type == 'imap'`, since `provider_for_host` is also `None` for an RSS or Paperless host. It is not stored: a column would be a second source of truth for a question already answerable from the row.

Two traps are written into the code because both fail quietly. The host must be parsed with `urlsplit(...).hostname`, **not** `.netloc` — `.netloc` carries the port, every `sources.url` is `imap://{host}:{port}/{label}`, and the flag would then be `true` for every mailbox. And `urlsplit` **raises** `ValueError` on an unmatched `[` rather than returning `None`; `EmailSourceRequest.host` is an unconstrained `str` interpolated verbatim into the url, so one user-creatable row would 500 the whole listing. Unknown host degrades to `true`.

**Known gap, closed by LUM-773.** The flag parses `sources.url`; the route matches the host in the decrypted credential payload. On the OAuth path both are built from one value inside one function. On the app-password path they are two independent client-supplied values — the url from `POST /me/email-sources`, the credential from a separate `PUT /connector-credentials/imap` — agreeing today only because the web client sends the same string to both. **LUM-773** enforces exact host+port equality on instance-bound IMAP credential PUT (`422 imap_host_mismatch`); see **[ADR-200](200-lum-773-mailbox-host-invariant.md)**.

## Alternatives Considered

* **Read `exc.kind` and keep `except Exception`** (what the ticket proposes) — fixes the named case and leaves a decrypt failure, an SSRF block and an internal `AttributeError` all reported as `422 imap_not_configured`, still unlogged. Rejected: the branches cost the same either way, and a partial fix here is the fourth iteration of the half-fix pattern that produced LUM-757 and LUM-759 themselves.
* ~~**Bundle the client-side fix**~~ — **reversed by the owner, 2026-08-01, and now part of the Decision.** The exploration argued for deferring it to a child of LUM-650: the empty `catch {}` is independent of the status code and swallows today's *correct* 422s identically, and bundling puts a copy decision inside a contract fix. The owner's counter is stronger: a mapping no client reads is not a fix, it is a contract nobody exercises. Both halves land together.
* **A global FastAPI handler for `CredentialUnavailable`** — rejected: neighbouring routes deliberately return `200 {ok:false}` for the same domain failures (`test_email_source`, `signals.py:457`), which a global handler would break.
* **`424 Failed Dependency`** — rejected: a 4xx, so it re-asserts client fault, and it has no client-side convention outside WebDAV. `503` carries the retry semantics that matter.
* **`200 {ok:false, error}`** mirroring `POST …/test` — rejected: that endpoint is an explicit test where failure is the expected outcome. A failed *mutation* returning 200 hides it from every generic client and monitor.
* **Do nothing, since the OAuth branch is unreachable through the UI** — rejected: three of the four wrong branches are reachable today on app-password mailboxes, and the missing log is an operator problem now.

Full detail: `.cursor/explorations/LUM-759-smtp-optin-error-mapping.md`.

## Consequences

**Easier**

* **A user who ticks "let Lumogis send as me" is told when it did not work.** Today the mailbox connects, sending is silently off, and they find out at the first send. This is the half of the chunk with a user in it.
* An operator debugging a failed send opt-in gets an exception type and a cause chain instead of one undifferentiated 422.
* A Lumogis bug in this path becomes a 500 with a traceback, so alerting can see it. Today it is a 4xx and invisible.
* When a send toggle for an existing OAuth mailbox does ship, the route is already correct rather than needing a fifth ticket.

**Harder**

* **`OAuthRefreshError.kind` now has three consumers with written mappings, not two.** A new kind must be considered against the health classifier, the send handler, **and** this route. ADR 196 already named this route as the unfixed third consumer; this closes it and makes the cost explicit.
* The route's response contract grows from two codes to seven. That is a genuine surface increase, justified by the alternative being one code that lies about six conditions.
* **A future reader may "simplify" the branches back into one catch**, or reorder them. `OAuthRefreshError` ⊂ `CredentialUnavailable`, so reversing those two clauses makes the first unreachable *silently* — every OAuth failure would answer `503 credential_unavailable`. A test asserting a kind-derived code, which fails under exactly that reordering, is the mitigation; the mapping table alone is not.
* **`_SMTP_OPTIN_HTTP` will `KeyError` if ADR 195 gains a code.** Deliberate. `kind` is coerced into `_KINDS` at construction (LUM-757), so a `KeyError` here can only mean the taxonomy moved and this table did not. It must not be softened to `.get(…, default)`.

**Future chunks must know**

* **The mapping is not this route's invention.** It comes from `routes/connector_credentials.py:29` and `signals.py:606`. A new case belongs in that contract first.
* **Transient means 5xx.** Do not add a 4xx for a condition the client cannot fix by changing the request.
* **`load_connection` makes a network call** for `auth_type="xoauth2"`. Any caller wrapping it in a `try` must assume outbound failure, not just decrypt failure. This is the mistake this ADR exists to correct, and it will be available to make again at every other call site.

## Revisit conditions

* **A UI ships that enables send on an existing mailbox** (child of LUM-641) → the OAuth branch becomes user-reachable; re-check the copy the client renders for each code, and whether `503` should offer a retry affordance.
* **ADR 195 splits `config` into user-entered and operator-owned** (LUM-760 is the likeliest forcing function) → the catch-site resolution collapses back into the table, and this ADR gets simpler.
* **A second route drifts from `connector_credentials.py:29`'s mapping** → promote that docstring to a shared helper rather than documenting it a third time.
* **ADR 196's kind set changes** → this table changes with it; the two are not independent.
* **`IETF draft-hays-http-528` (Outbound Dependency Failed) reaches RFC status and clients understand it** → revisit the `network`/`config` rows, which are currently `503` for want of anything more precise.

## Status history

- 2026-08-01: **Amended by LUM-772** — the standalone-password refusal, found by code review during LUM-771's verification and fixed in the same session. Three mutation checks, all killed: dropping the refusal, applying it to every OAuth mailbox rather than only the standalone-password shape (which would break the case LUM-771 had just fixed), and silently nulling the password instead of refusing.

- 2026-08-01: **Amended by LUM-771** — the 200-body error vocabulary, which LUM-759 left alone. Split out of LUM-769 after three critique rounds and given an amendment rather than a second ADR, since 197 already owns *"the SMTP opt-in route answers with the credential layer's own error contract"* and `smtp_oauth_denied` extends exactly that; a second ADR for one route's error contract would be the fragmentation ADR 195/196/197 exist to avoid. The revisit condition below — *"a UI ships that enables send on an existing mailbox → the OAuth branch becomes user-reachable"* — fired, and found the branch **could not succeed at all**: the route never forwarded `auth_type`/`access_token`, so an OAuth mailbox verified with an empty password. Because the device flow auto-enables SMTP at connect, off worked and on did not. Seven mutation checks; six killed. **The one survivor is worth recording**: `getattr(imap, "auth_type", "app_password")` — the silent default this decision exists to remove — passes all 29 route tests once the fakes carry the attribute. It is a code-review property, not a test-provable one, and the plan claimed otherwise. The defect existed in the first place because 24 route tests shared one app-password fake with no `auth_type` field at all, so LUM-759 could cover this route's OAuth *error* branches exhaustively and never run its happy path.

- 2026-08-01: **Accepted on verification of LUM-759.** Implementation confirmed the decision and sharpened one part of it. The four documented raise types map as specified; the `except` ordering is pinned by a test that fails when the clauses are reversed; the setup form renders the codes and still connects the mailbox. **Five mutation checks were run and all killed**, two of which reproduce defects this chunk's own review history produced — folding `CredentialUnavailable` back into the classifier branch (the self-review P1) and an `err.message` fallback in the copy function. **A sixth mutation exposed a weak test before it shipped**: the sentinel leak test used a *recognised* code, so the default branch — the exact place an `err.message` fallback would live — was never exercised, and planting the leak left it green. **One defect was found by code review at verification, not by any test**: the notice had no reset at submit start, so it survived a later successful attempt. 30 tests; ADR 195's taxonomy needed catch-site resolution in two places, not one, exactly as the Decision anticipated.

- 2026-08-01: **Three decisions taken by the owner.** (1) Narrow the catch rather than only reading `kind`. (2) **Fix the client's empty `catch {}` in the same chunk** — reversing this ADR's own "file it against LUM-650" recommendation, on the grounds that a mapping no client reads is not a fix. (3) `credential` gets a distinct `imap_reauth_required` code, because (2) makes the code render as copy and `imap_not_configured` is still returned in the same chunk for a condition with the opposite fix.

- 2026-08-01: Draft created by `/explore` LUM-759. Impact scan found no 🔴/🟡 tickets; no Linear block edges registered.
