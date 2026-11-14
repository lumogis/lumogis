# ADR-218 — Connector test-connection: a probe registry with one entry

**Status:** Accepted
**Date:** 2026-08-23
**Ticket:** LUM-834 (chunk A — G1 test-connection, G2 typed Paperless form)
**Relates to:** ADR-199 (avoid decrypt-on-enumerate), ADR-214 (sending address), ADR-216 (LUM-838 RSS/page outbound policy), LUM-741 (bounded resolver), LUM-650 (SMTP opt-in pre-save test), LUM-846 (CalDavForm destroys the stored secret)
**Deferred to a sibling:** the CalDAV probe, the admin mirror, "leave blank to keep", and chunk B's `enabled` / field-presence state model

## Context

The connector manager had two P0 gaps. A user could save a credential and had no
way to learn whether it worked, and `paperless` fell through the form registry to
a raw-JSON textarea — so configuring it meant knowing the payload shape by heart.

Two connection tests already ship, and neither is reusable here:
`POST /me/email-sources/test` (IMAP, pre-save, LUM-644) and the SMTP opt-in's
pre-save connect test (LUM-650). Both are **pre-save** and **per-mailbox**. What
was missing is the post-save, per-connector question: *does the credential I
already stored still work?*

## Decision

### 1. A dispatch-by-connector-id registry, shipping with **one** populated entry

`services/connector_probes.py` owns `probe(connector, user_id) -> ProbeOutcome`.
Chunk A populates exactly one row — `paperless`.

**Why a registry for one probe.** The dispatcher already resolves two non-default
cases before any second probe exists (`paperless` probed, `imap` redirected), and
`smtp` and `system_smtp` needed a third and fourth immediately. It is a dispatch
table with one probe, not a probe wearing a registry's name.

**Why only one.** Four independent review findings converged on CalDAV:
`caldav_credentials.load_connection(user_id)` has no parameter to thread a bounded
resolver through (`:88`), it **tier-walks user → household → system** so a
member's "test" would verify a credential that is not theirs, its probe
client/verb/timeout were unspecified, and `caldav` is an optional guarded import.
Rather than answer four questions to add a second row, CalDAV became a follow-up.

### 2. Dispatch is stated per id — never as "everything else"

An implicit default is *correct for one member and silently wrong for another*.

| id | Result | Why |
| --- | --- | --- |
| `paperless` | probed | the one populated entry |
| `imap` | `tested_elsewhere` | `POST /me/email-sources/test` does it properly |
| `smtp` | `tested_elsewhere` | live-tested by the SMTP opt-in (LUM-650) |
| `system_smtp` | **422 `use_system_mail_admin`** | every sibling verb refuses it the same way |
| `caldav`, `ntfy`, `llm_*`, all others | `unsupported` | no probe exists |

`smtp` is the case that proves the rule: it *looks* unprobed, so a catch-all
default would have told users "no connection test for this connector" about a
connector that has one. `system_smtp` is the other — it is hidden from
`GET /registry` so no card renders, but it passes registry membership and is
reachable by direct POST. A `200 unsupported` there would have made this the one
verb on the credential surface that **answers** where its siblings **refuse**.

### 3. `ok` is tri-state, permanently

`true` verified · `false` tried and failed · **`null` not attempted**.

Collapsing `null` into `false` paints a red ✗ on a connector that is working
perfectly. This is the arc's recurring *a surface that says the wrong thing*
defect, and chunk B's four-state chip (`Not configured` → `Configured (untested)`
→ `Verified` / `Failed`) is built on the three states. `code` is a Pydantic
`Literal`, not a `str`, so the shape freezes into `openapi.snapshot.json` as a
contract rather than widening silently later.

### 4. `detail` is a fixed string chosen by `code` — never an exception message

This is a deliberate **departure from the nearest precedent**.
`routes/signals.py:_safe_probe_error` is `str(exc)[:200]` — **truncation, not
sanitisation**. Several `ValueError`s off this credential surface embed the
configured host, and `services/outbound_http_url.py:210` can embed a resolved
**IP**; keeping the first 200 characters stops none of that. Exception **type**
only in logs, matching `sending_addresses` and `paperless_overlap.username_skip`.

`_safe_probe_error` is left alone on its own route — the input there is the
user's own just-typed host, a narrower exposure. Recorded as an open follow-up
rather than changed as a drive-by.

### 5. A failed test is a `200`, never a `5xx` — and the catch that makes that true

"Your paperless server rejected this token" is an ordinary answer, not a server
error. The guarantee has a named owner: a broad `except Exception` at the route
maps anything nobody enumerated to `unreachable`. Without an owner the guarantee
is a wish — any unenumerated exception type becomes a 500.

### 6. The budget spans **two separately bounded phases**

`LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S` (default 5.0s, floor 0.5s) is a budget, not a
timeout.

* **Phase 1** — the credential load runs the SSRF check, which resolves the host
  over DNS **untimed by default**. Bounded by a deadline-derived `resolve_host`,
  reusing LUM-741's `_bounded_resolver` rather than copying it.
* **Phase 2** — the probe's own HTTP call, handed the **remaining** budget.

**Bounding the resolve is not bounding the probe:** a host that resolves and then
never answers hangs forever otherwise. And within phase 2, `httpx.Timeout(x)`
bounds each *phase* (connect / read / write / pool) independently rather than the
call — so the probe halves its budget across connect and read, making the
advertised ceiling an actual one. Latency target: **p99 ≤ 5.5s**, slow for a
click and bounded on purpose rather than hidden.

### 7. Redirects are refused, by keyword rather than by library default

`services/outbound_signal_fetch.py` already records why: validating an entry URL
and then following redirects is *"a partial fix that reads like a complete
one"* — the first hop is checked and every later hop is not. That module needs
hop-by-hop re-validation because feeds legitimately redirect. A probe does not:
the user typed their own address, and a 3xx means "could not verify".

### 8. No admin mirror — and the reason is security, not scheduling

`probe(connector, user_id)` has **no tier axis**, so an admin route would have
tested *the caller's own* credential while presenting as a tier test.
`AdminConnectorCredentialsView` renders the shared card across three tiers, so two
of them would have shown a button that silently answered the wrong question. The
web enforces this too: the Test affordance renders only on the `/me` base path.

### 9. Both form fields are required on **every** save

`put_payload` is whole-payload replacement (`ciphertext = EXCLUDED.ciphertext`,
no merge). A form that omits the secret on edit **destroys the stored secret** —
and `CalDavForm`'s `if (pass.length > 0)` has exactly that bug where it already
ships, filed as **LUM-846**. "Leave blank to keep" becomes honest only once the
backend can merge, which is chunk B's field-presence work.

Validation is owned in code rather than by a `required` attribute, and there is
one mechanism on purpose: `required` blocks the submit before the code guard
runs, making it a dead branch plus a native bubble reading *"Please fill in this
field"* — where what the user needs to be told is that saving replaces the whole
credential. `NtfyForm` is the in-repo precedent.

### 10. Audit records **that** a test ran, never what it was given

`ACTION_CRED_TESTED` is emitted **in the service**, per the route file's stated
convention. **Not-attempted outcomes are not audited at all** (`unsupported`,
`tested_elsewhere`, `not_configured`): nothing was tested, so there is no event,
and auditing non-events would make *"was this credential ever verified?"*
unanswerable.

## Alternatives considered

**A uniform `load_connection(user_id)` dispatch**, as the design spec's Phase 1.2
described. **Rejected — it does not hold.** The three loaders have different
signatures: `caldav(user_id)`, `paperless(user_id, *, resolve_host)`,
`email(user_id, *, instance)`. IMAP is multi-instance and requires a mailbox id
a connector-level path has nowhere to put. The registry dispatches per connector
with each probe calling its own loader correctly.

**Testing IMAP through this route.** Rejected — `POST /me/email-sources/test`
does it better (pre-save, folder detection, INBOX count, `proxy_unverified`).

**Reusing `_safe_probe_error`.** Rejected — see decision 4.

**Widening `caldav_credentials.load_connection` in this chunk** to ship a second
probe row. Rejected: it fixes only one of CalDAV's two prerequisites, and the
other (what a member's tier-walking test *means*) is a design question, not a
signature change.

## Consequences

1. **Every future connector gets `unsupported` for free** — never a false
   negative. The registry is the extension point; document its shape when adding
   the second entry, not the fifth.
2. **Chunk B needs something this chunk deliberately does not build.**
   "Configured (untested)" requires a per-user *last tested* notion, which is not
   persisted here. `system_smtp`'s `last_test_ok_at` (`connectors/registry.py:266`)
   is the shipped precedent to follow rather than invent.
3. **Nothing is added to `ConnectorCredentialPublic`.** Field-presence is chunk B,
   and it is where the ADR-199/214 "no decrypt on the management surface"
   question actually has to be answered. Keeping it out of A means A ships
   without that argument.
4. **The limiter is the only guard against stacking.** There is no concurrency
   control, so ten clicks in a minute is ten in-flight probes each holding a ~5s
   budget. Stated so it can be objected to rather than discovered.
5. **The CalDAV follow-up carries two prerequisites, not one** — widen the loader
   *and* decide what a member's test means given the tier walk.
6. **Decision 9 turned out to describe a bug that already shipped, three times.**
   Requiring the secret on every save was written here for the *new* Paperless
   form. `CalDavForm` was known to have the defect (LUM-846). Acting on that
   ticket found `ImapForm` carried the identical two lines and `NtfyForm` a
   variant — and the per-form tests for two of them were **green while pinning
   the destroy as intended behaviour**. All four forms now hold the rule, and
   the guard is a registry-level test rather than four per-form ones, because
   the failure mode is the form somebody adds next. See LUM-846.

   **And the registry-level guard has a blind spot worth naming**: it walks
   `credentialFormRegistry`, which by construction contains only the *typed*
   forms — so it cannot see `JsonFallbackForm`, which is what an unregistered
   connector actually gets. Reviewing the LUM-846 fix found the fallback had a
   worse version of the same defect: it seeds from `{}` (the server never sends
   payloads back) and had no submit button, so `onBlur` — which on the
   connectors surface **is the PUT** — replaced the whole credential with `{}`
   when a user merely clicked into the box and away again. Fixed there too. The
   lesson is the one this arc keeps producing: a guard is only as wide as the
   collection it iterates, and the interesting case is usually the one outside it.
7. **One cross-language duplication ships**, by necessity: the `tested_elsewhere`
   set exists in Python and in TypeScript. A backend test reads the TSX and pins
   the two equal — the LUM-839 remedy, because a comment saying "must not drift"
   enforces nothing.
