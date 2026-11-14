# ADR-200: Mailbox IMAP host invariant (sources.url vs credential payload)

**Status:** Accepted
**Draft history:** `.cursor/adrs/LUM-773-mailbox-host-invariant.md`
**Created:** 2026-08-02
**Last updated:** 2026-08-02 (accepted on verification of LUM-773)
**Decided by:** `/explore LUM-773`
**Linear:** [LUM-773](https://linear.app/lumogis/issue/LUM-773/p2-a-mailboxs-sourcesurl-host-and-its-imap-credential-host-are-two)
**Relates:** ADR-197 (known-gap paragraph closed), LUM-771/LUM-772 (same email opt-in lane), LUM-634 (plaintext host enumeration should read `sources.url`)

## Context

For app-password mailboxes, `sources.url` (`imap://{host}:{port}/{label}`) and the IMAP connector credential payload (`host`, `port`) are written in two HTTP steps. `GET /sources` derives `smtp_host_required` from the url; `set_email_smtp` and IMAP polling use the credential. They agree today only by client discipline. ADR-197 Decision 5 documents this gap and defers a server invariant to LUM-773.

OAuth mailboxes are safe: both values are built from `flow['host']`/`flow['port']` in one function.

## Decision

Treat **`sources.url` as the canonical host and port** for an instance-bound IMAP mailbox. On `PUT /me/connector-credentials/imap?instance={source_id}` (and the admin equivalent), **reject** a payload whose `host` or `port` does not **exactly** match the owned source row's url — after normalizing host with `strip().lower()` and parsing port with the same `.hostname` / default-993 rules used elsewhere. This is **string equality**, not provider-equivalence (two hosts that classify identically are still rejected if they differ). Return **`422`** with code **`imap_host_mismatch`**. Do **not** silently overwrite client-supplied host/port.

Skip validation when `instance` is empty (legacy single-mailbox env fallback with no source row).

Shared parsing lives in `orchestrator/services/imap_source_url.py`:
- `imap_hostname_from_source_url` — host-only, used by `_smtp_host_required` (port errors must not flip classification).
- `imap_host_port_from_source_url` — host+port, used by credential validation and paperless overlap.

## Alternatives Considered

* **Silent derive on PUT** — rejected: masks client integration errors; inconsistent with LUM-772 refusal-over-drop precedent.
* **Atomic POST merging source + credential** — rejected: disproportionate contract change for P2 polish.
* **Validate only at `set_email_smtp`** — rejected: allows inconsistent stored state and lying `smtp_host_required`.
* **New persisted host column** — rejected: duplicates `sources.url`; ADR-197 rejected stored `smtp_host_required` for the same reason.

Full detail: `.cursor/explorations/LUM-773-mailbox-host-invariant.md`

## Consequences

**Easier**

* `smtp_host_required`, `set_email_smtp`, and IMAP credentials cannot disagree about provider classification for the same mailbox.
* Second clients and API scripts get an explicit 422 instead of confusing send-opt-in behaviour.
* Completes ADR-197's deferred invariant without a new database column.

**Harder**

* API users must send matching host/port on credential PUT or fix the source row first (no silent fix).
* Shared url parsing is deduplicated across `_smtp_host_required`, `paperless_overlap._mailbox_host_port`, and the new check.

**Future chunks must know**

* Any code that writes IMAP credentials with a non-empty `instance` must pass the guard.
* LUM-634 plaintext host metadata should read from `sources.url`, not credential payload host.

## Revisit conditions

* If Lumogis merges mailbox create into a single atomic endpoint, revisit whether step-2 validation moves into a transactional create helper.
* If internationalized domain hosts are accepted in `EmailSourceRequest.host`, revisit normalization (IDNA/punycode) beyond `lower()`.

## Status history

- 2026-08-02: Draft created by /explore LUM-773
- 2026-08-02: Revised during /review-plan --arbitrate R1 — Decision clarified to exact host+port string equality (not provider-equivalence)
- 2026-08-02: Accepted by /verify-plan — implementation confirmed (LUM-773)
