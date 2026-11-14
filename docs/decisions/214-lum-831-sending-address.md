# ADR-214: The sending address is resolved on demand, never stored a second time

**Status:** Finalised
**Created:** 2026-08-15
**Last updated:** 2026-08-15 (finalised)
**Decided by:** `/explore LUM-831`
**Exploration:** `.cursor/explorations/LUM-831-sending-address.md`
**Linear:** LUM-831 (blocks LUM-830, LUM-778; relates ADR-199 / LUM-634, ADR-197 / LUM-773, ADR-213)

## Context

LUM-830 shipped a From picker for composing a new message, labelled with the
mailbox **name**. Two problems:

* **`sources.name` is not unique.** The schema constrains `UNIQUE(user_id, url)`
  and nothing on `name`, and an unnamed mailbox is auto-named
  `Email ({mailbox_label})`. Two mailboxes can be indistinguishable in the
  picker.
* **The name is not what recipients see.** Every send site sets
  `from_addr=conn.username`, and `set_email_smtp` states the From address is
  always the mailbox's authenticated account, never client-set.

The address lives inside the Fernet credential ciphertext. There is no username
column, and `GET /api/v1/me/connector-credentials` is deliberately metadata-only
with no decrypt path — the posture ADR-199 (LUM-634) established and LUM-778 is
still completing.

## Decision

**Resolve the sending address on demand from the credential, through the
existing `mailbox_account_address` accessor. Do not store it a second time.**

A narrow, owner-scoped endpoint returns `{source_id, address}` for the caller's
SMTP-enabled mailboxes; the composer requests it when it opens.

**Four points settled by Thomas, 2026-08-15:**

* **Format is `Name — address`** — e.g. `Work — alice@acme.com`. Not the RFC-ish
  `Name <address>` the prior art uses: that form assumes the name is a
  *person's*, and a Lumogis mailbox name is a label the user typed, so
  `Work <alice@acme.com>` reads as a person called "Work".
* **A mailbox whose credential will not decrypt shows name-only and stays in
  the picker.** Dropping it would remove a mailbox the user can genuinely send
  from — the LUM-719 mistake in a new place. A decrypt failure is usually key
  rotation, not a broken mailbox.
* **The sending address is owner-only.** Not household-visible on any surface.
  A shared signal already exposes the *sender* of received mail; a member's own
  *sending* address is an account identifier, and no surface needs it.
* **No interim.** Disambiguating colliding names with the IMAP host was
  considered and rejected: it would put `imap.gmail.com` in front of people —
  answering "which of these two" without answering "what will my recipient
  see" — and be replaced by this within weeks. Two changes to the same row, the
  first throwaway.

This is not a new pattern. `services/email_credentials.mailbox_account_address`
was added by LUM-705 as *"the supported way to learn 'which address am I?'
without holding a credential"*, and `routes/api_v1/email_send.py` already calls
it per request so reply-all can drop the user from their own Cc. This decision
gives that accessor a second caller.

## Alternatives Considered

- **A plaintext `sources.send_address`, written at SMTP-enable time** — the
  cheapest read, and `sources` is not the table ADR-199 forbade. Rejected
  because it starts a **second copy** of a value with no canonical plaintext
  home. ADR-199 could choose url-first enumeration precisely because
  `sources.url` was *already* canonical for the host (LUM-773); there is no
  equivalent for the account, so this is not "read what we store" but "store it
  twice", which re-opens the divergence class ADR-197 closed — a credential
  re-saved with a different account leaves the column silently wrong.
- **Bulk read-time decrypt over the caller's mailboxes** — the enumerate shape
  ADR-199 removed and LUM-778 is deleting. Smaller blast radius than the
  household allow-file generator, but adopting the shape while a sibling ticket
  removes it is the wrong thing to leave in the codebase.
- **Append the IMAP host to colliding names** — fixes ambiguity with no
  credential read at all, but `imap.gmail.com` is not what a recipient sees.
  Viable as an interim, not as the answer.
- **Make `sources.name` unique per user** — renames existing mailboxes at
  migration time to fix a label, and still shows a label.

Full comparison and the prior-art survey: the exploration file.

## Consequences

**Easier**
- The displayed address **cannot drift** from the address the send uses: both
  read the same credential. That is the property that matters for a control
  whose failure mode is sending from the wrong account, and it is free here.
- No migration, no export/remap registry change, no new dependency.
- The credential-management surface stays decrypt-free, so this does not
  pre-empt or contradict LUM-778.
- Duplicate mailbox names stop being ambiguous without constraining `name`.

**Harder / foreclosed**
- **A plaintext `sources.send_address` is off the table.** Any future surface
  needing the address *without* a decrypt — the mailbox settings list, anything
  on the egress path — must revisit this rather than quietly add the column.
- **N decrypts per composer open**, N being the caller's SMTP-enabled mailboxes
  (typically 1–2). Cheap, but non-zero, and it is a decrypt on a UI interaction.
  **Corrected 2026-08-15 (`/review-plan --arbitrate` R1):** the original wording
  here — *"a Fernet decrypt, not a refresh round-trip"* — was **wrong about the
  cost**, and wrong in the direction that mattered. `mailbox_account_address`
  went through `load_connection`, which also performs a **live `getaddrinfo`**
  per mailbox (`_connection_from_payload:483`), an SSRF policy walk, a TLS check,
  and for an xoauth2 mailbox mints and **persists** a token — making a display
  GET into a write with unbounded network latency. The accessor is therefore
  **narrowed to a payload username read**: `get_payload` →
  `_require_str(payload, "username")`, which is where the username came from all
  along (`_connection_from_payload:451`, above every one of those steps).
  After the change the cost is genuinely one DB read plus one Fernet decrypt per
  mailbox, and the claim above is true rather than aspirational.
- **The accessor no longer validates the mailbox host**, because it no longer
  needs to connect. A mailbox whose host has become unresolvable or
  SSRF-blocked now still reports its address. That is correct — *"which address
  am I?"* has an answer even when the mailbox cannot currently be dialled — and
  the send still fails loudly at send time with a real error. It also removes a
  transient failure class: a flaky resolver previously turned into per-mailbox
  omissions, which would have made the degraded note flicker between refetches.
- **Sendability is the SMTP row alone.** The IMAP row is a runtime dependency of
  *resolving* the address, not part of the predicate — so backend and web ask the
  same question (`hasSmtpCredential`) rather than two similar ones. Precedent
  LUM-778 should be read against.
- **A mailbox whose credential will not decrypt must degrade to name-only, never
  disappear from the picker** — dropping it would remove a mailbox the user can
  genuinely send from, which is the LUM-719 mistake in a new place.

**What future chunks must know**
- `mailbox_account_address` is the one supported way to learn a mailbox's own
  address. Do not re-derive it, and do not cache it in a column.
- The address is owner-only. It is not household-visible and no surface should
  widen that without a decision.

## Revisit conditions

- **A surface needs the sending address without being able to decrypt** — e.g.
  the egress allow-file path, or a settings list that must stay metadata-only.
  That is the case this decision trades away, and it is the honest trigger to
  reopen the denormalisation option.
- **Composer-open latency becomes measurable** with many sendable mailboxes —
  the decision assumes 1–3, matching the household shape ADR-213 sized for.
- ~~**LUM-778 concludes that a plaintext username column is right after all** for
  the paperless case. Then the two decisions should be reconciled rather than
  left pointing opposite ways.~~ **Closed 2026-08-22 — it concluded the
  opposite.** LUM-778 converged `paperless_overlap._mailbox_username` onto this
  accessor instead of storing a second copy; there is now one implementation of
  "read a mailbox's username" and this ADR's *"do not re-derive it"* is true
  rather than aspirational. See the status-history entry below.
- **An address becomes available for a mailbox that has never had sending
  enabled** — this decision only covers SMTP-enabled mailboxes, because those
  are the only ones a From row offers.

## Status history

- 2026-08-22: **The last re-derivation removed (LUM-778).** This ADR said
  `mailbox_account_address` was *"the one supported way to learn a mailbox's own
  address"* while `paperless_overlap._mailbox_username` was a second one — the
  ADR created that divergence itself, by narrowing the accessor to a payload
  username read on 2026-08-15 and so making the two functions converge in
  everything but error shape. They agreed on a normal mailbox, which is exactly
  why nobody noticed; LUM-813 exists because `_message_id_for` shipped twice and
  the incomplete copy won.

  Two behaviours were reconciled deliberately rather than by whichever shape was
  convenient:

  - **The legacy `instance=""` row** — a mailbox configured before LUM-170,
    which migration 054 left at the `DEFAULT ''` and which no write path can
    recreate — was only read by the paperless copy. It moved **into** the
    accessor, because a fallback that only one caller has is the divergence
    restated. It is now **guarded to `AUTH_ENABLED=false`**: unguarded, it
    answered "what address is mailbox X?" out of mailbox Y's credential, and fed
    that to the overlap `high`/`low` decision where `high` gates.
  - **Raise vs `None`** — the accessor keeps raising. That is the contract, and
    its three callers each decide differently and correctly (omit the mailbox /
    degrade to an unfiltered Cc / skip it). The overlap path's *"skip, don't
    sink"* is now one `try` at the call site rather than a second function.

- 2026-08-15: **Finalised by `/verify-plan`** — implementation confirmed the decision. Two refinements the implementation revealed, both recorded above rather than buried: the accessor was **narrowed to a payload username read** (the ADR's own cost model had been wrong — `load_connection` performed a live `getaddrinfo`, an SSRF walk and, for xoauth2, a token mint-and-persist, none of which a username needs), and **sendability is the SMTP row alone**. Neither changes the decision; both make the recorded consequences true. Verified: no connection path lost a guard — `email_imap_source.py:218` and `load_smtp_connection` each still call `load_connection` in full.

- 2026-08-15: Draft created by `/explore` (LUM-831)
- 2026-08-15: Format, decrypt-failure fallback, visibility scope and the no-interim call settled by Thomas; open questions closed.
- 2026-08-15: Revised during `/review-plan --arbitrate` R1 — the Consequences cost model was wrong (it omitted the per-mailbox live DNS, SSRF walk and OAuth token write that `load_connection` performs). The decision is unchanged; the accessor is narrowed to a payload username read so the recorded cost becomes true. Sendability narrowed to the SMTP row alone, closing a backend/web predicate divergence before it opened. Raised by Fable 5 (D1.1/D7.1, D8.1).
