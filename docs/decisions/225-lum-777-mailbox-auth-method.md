# ADR-225: Mailbox auth method on `GET /sources`, derived from `configured_keys` (LUM-777)
**Status:** Finalised
**Created:** 2026-11-06
**Last updated:** 2026-11-06
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-777`), revised at `/review-plan` arbitration R1
**Finalised by:** /verify-plan 2026-11-06 (claude-opus-5-5)
**Linear:** LUM-777
**Amends:** ADR-195's LUM-760 amendment (its revisit condition is met)

## Context

The web client could not tell an OAuth mailbox from an app-password one. So the `config` and `credential` poll-health copy hedged (LUM-760), and so did the send-disable confirm (LUM-769). Only the server's `smtp_oauth_denied` rename stopped the client from offering an OAuth user a password field. No client test could protect that seam, because no fixture could be an OAuth mailbox.

The method is recorded inside the encrypted IMAP payload as `auth_type`. ADR-199 forbids decrypting payloads to enumerate them, and LUM-777 asked for both at once.

Since ADR-219 (migration 079), `put_payload` writes the payload's key **names** to the plaintext `user_connector_credentials.configured_keys`, in the same statement as the ciphertext. The column is NULL for rows written before 079, and NULL means unknown.

## Decision

`GET /sources` publishes `auth_method: "oauth" | "app_password" | null` on every row. It is derived on read and never stored.

- **One read per request.** `list_sources` calls `email_credentials.imap_auth_methods(user_id)` once, and only when the listing holds at least one IMAP row. That is one `connector_credentials.list_records` `SELECT`, which never selects ciphertext. Nothing is decrypted.
- **The classifier** is `email_credentials.mailbox_auth_method(configured_keys)`, in the module that owns the IMAP payload parser it mirrors. Rules, in order:
  - NULL keys → `null` (unknown);
  - both `refresh_token` and `password` → `null` (contradictory; the generic credential `PUT` can write that shape);
  - `refresh_token` → `oauth`;
  - `password` → `app_password`;
  - anything else → `null`.
- **`null` and absent mean unknown, never app-password.** This reverses the ticket's original "absent = app-password" wording on purpose. A spurious `app_password` on an OAuth mailbox brings back the password-field trap. A spurious unknown only shows the hedged copy. Non-IMAP rows, a missing credential and a credential read failure are all `null`.
- **Degrade.** A credential read failure leaves the listing at 200 with every `auth_method` `null`, and logs the exception **type name** only.
- **One enumeration filter.** `email_credentials.mailbox_records(user_id, connector)` is the single "this connector, non-empty instance" filter over `list_records`. `list_mailboxes`, `sendable_mailboxes`, `declared_imap_hosts`, `smtp_transport.declared_smtp_hosts` and `imap_auth_methods` all use it.
- **Display-only.** The server-side guards (`smtp_oauth_denied`, `smtp_password_not_applicable`) keep reading the decrypted `ImapConnection.auth_type`.

The web client uses the field in three places:

- **`pollHealthMessage(code, authMethod)`** names one fix-owner for `credential` and `config` when the method is known. Unknown keeps the two-owner copy, byte-identical.
- **The send-disable confirm** drops its password sentence for `oauth` only. An OAuth mailbox never uses a stored sending password, because `load_smtp_connection` authenticates it with its token. Every other mailbox keeps the conditional sentence.
- **`MailboxSendControl`** remaps `smtp_auth` to `smtp_oauth_denied` for an `oauth` mailbox, so it never shows a password field, and calls `console.warn`. This hides a server regression behind plausible copy. That is a deliberate trade, and the server test `test_smtp_send_route.py` plus the warning keep the regression visible.

### Amends ADR-195 (LUM-760 amendment)

ADR-195's LUM-760 amendment said `config` could not be split because no auth field was published, and made "if the auth method is ever published, `config` should split" its revisit condition. That condition is now met, and `config` and `credential` split by method. The standing rule still holds: **a code that spans two fix-owners must name both** whenever the owner cannot be named, which is the unknown case.

## Alternatives Considered

Full detail is in the LUM-777 exploration.

- **Client-only derivation from `configured_fields`:** it works, but it puts the payload-schema rule in the client and makes every consumer fetch the credential list.
- **A new plaintext `auth_type` column (or `sources.auth_method`):** a second source of truth for a fact `configured_keys` already holds. Its existing rows would be NULL anyway.
- **Decrypt each payload in `list_sources`:** the decrypt-on-enumerate that ADR-199 removed.
- **Lazy heal of NULL `configured_keys` from the poller's decrypt:** it adds a write to a generic read primitive used by every connector, for a population no release contains. Deferred, see Revisit.
- **A decrypting backfill migration:** it decrypts every credential to serve maintainer installs only.
- **Discriminate on `"auth_type" in keys`:** both kinds of payload carry that key, and `_connection_from_payload` defaults it to `app_password` when absent.
- **Absent reads as app-password (the ticket's original wording):** rejected, see Decision.
- **Also publish `smtp_own_password`** (whether the SMTP row holds its own password), so the confirm could branch on it. Dropped at arbitration R1 for two reasons. Key names give no positive evidence, because `load_smtp_connection` defaults `reuse_imap` to true. And the field would have come from the sources query while the send state comes from the credentials query, so the two could disagree.

## Consequences

- **Easier:** the copy names the right fix-owner, and the disable confirm drops the password warning for OAuth. The client has its own "never show a password field for OAuth" guard, tested against a real OAuth fixture.
- **Cost:** at most one extra metadata query per `GET /sources`, none for a listing without mailboxes, and zero decrypts. `test_listing_reads_credentials_once_and_never_decrypts` makes every decrypt primitive fail, through the real `list_records` path.
- **Coupling:** `configured_keys` gains a second consumer after the connector card. Renaming `refresh_token` or `password` in the IMAP payload, or no longer writing `configured_keys`, degrades the field to unknown. That is safe, and `test_imap_writer_payloads_classify_through_configured_keys` makes it loud.
- **Accepted limitation: key names cannot see values.** A hand-written payload whose keys disagree with its `auth_type` value can be misread. For example, `{auth_type: "xoauth2", password, …}` without `refresh_token` reads as `app_password`. Every such row already fails to connect (`get_access_token` needs `refresh_token`, and `_connection_from_payload` needs `password`), so the classifier only has to be right for rows that can work.
- **Pre-079 rows** stay `null` and show today's hedged copy:
  - an OAuth row heals at its first **successful** token refresh, so a pre-079 OAuth row that keeps failing stays unknown;
  - an app-password row heals only when it is saved again.

  Only maintainer installs can hold such rows: no released line carries email together with a pre-079 credential store.
- **Must not:** use `auth_method` for authorisation, coerce `null` to a known value, or compute it per row with a query or a decrypt inside the loop.

## Revisit conditions

- A **release** ships IMAP credentials without `configured_keys`, for example a scoped promotion that carries email without migration 079. Then add the lazy heal before that release: heal NULL `configured_keys` from the decrypt `get_payload` already does, guarded by `IS NULL`.
- A second xoauth2 writer appears that does not store `refresh_token` (such as a service-account or app-only grant), or the IMAP payload gains a third auth mode. The classifier and its round-trip test must change together.
- `GET /sources` moves to pagination or a per-row loader. Keep the credential read to one query per request.

## Status history
- 2026-11-06: Draft created by /explore (headless, run-workflow LUM-777)
- 2026-11-06: Revised at /review-plan arbitration R1: `smtp_own_password` dropped, `mailbox_records` added, pre-079 heal claims corrected
- 2026-11-06: Finalised by /verify-plan. The implementation confirmed the decision.
