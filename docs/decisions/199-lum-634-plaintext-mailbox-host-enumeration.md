# ADR-199: Plaintext mailbox-host enumeration (avoid decrypt-on-enumerate)

**Status:** Finalised
**Created:** 2026-08-02
**Last updated:** 2026-08-02
**Decided by:** /explore (LUM-634, headless); finalised by /verify-plan

## Context

The LUM-625 Layer-2 email-IMAP egress allow file and the LUM-571 process-wide egress ceiling are built from the household union of declared mailbox hosts. The enumerators (`email_credentials.declared_imap_hosts`, and the IMAP-host input of `resolve_smtp_host_port` / `smtp_transport.declared_smtp_hosts`) previously **decrypted** each member's IMAP/SMTP credential payload solely to read a non-secret hostname. The Pass-1/2 review flagged this as an avoidable secret-handling surface; since LUM-633 a decrypt failure at enumeration time silently dropped a host and surfaced a misleading `egress_blocked` to the user.

The issue proposed a new plaintext host **column** on `user_connector_credentials`. In parallel, LUM-773 decided `sources.url` is the **canonical** mailbox host+port and recorded that LUM-634 should read from it. `sources.url` already stores the host in plaintext; the credential `instance` is the `sources.id`, so every enumerated row already has a source row to read.

## Decision

Enumerate mailbox `(host, port)` for the egress allow file / ceiling by **parsing `sources.url`** (keyed by `instance == sources.id`), not by decrypting the credential payload. Retain the existing payload decrypt as a **fallback only when the source row is missing or `sources.url` is unparseable** — not on every enumeration when url parses. The ADR's *optional* divergence-reconcile decrypt (when url and credential host differ pre-LUM-773) is **not implemented in LUM-634**; divergence detection is deferred to LUM-773's invariant. Apply url-first enumeration to `declared_imap_hosts` and to the imap_host input of the SMTP resolver.

Do **not** add a plaintext host column to `user_connector_credentials` (that would duplicate `sources.url` and re-open the host-divergence class LUM-773/ADR-197 close). The paperless `_mailbox_username` decrypt (a username, not a host, and absent from `sources.url`) is **out of scope** and moves to a sibling ticket. `email_oauth.declared_oauth_hosts` is already decrypt-free and unchanged.

## Alternatives Considered

* **New plaintext `host`/`port` column on `user_connector_credentials`** (issue's original wording) — rejected: introduces a second source of truth for a host that already lives in `sources.url`; contradicts LUM-773 / ADR-197.
* **Hybrid column-as-cache with `sources.url` canonical** — rejected: migration + cache-coherence cost with no household-scale benefit over a DNS-free string parse.
* **Close as won't-fix / keep decrypting** — rejected: retains the flagged surface and a real user-facing failure mode (phantom `egress_blocked`).
* **Mirror the SMTP submission host to plaintext to make the "Other" branch decrypt-free** — rejected for this chunk: same second-source-of-truth risk for marginal gain; swap only the imap_host input.

Full detail: `.cursor/explorations/LUM-634-plaintext-mailbox-host-enumeration.md`

## Consequences

**Easier**
* Allow-file / ceiling regeneration touches no password ciphertext to read a hostname on the url-hit path (closes the review finding).
* Removes the phantom-`egress_blocked` failure mode where an enumeration decrypt failure dropped a valid host.
* No migration; reuses the existing `sources.url` parse via shared `orchestrator/services/imap_source_url.py`.

**Harder**
* Correctness of reading `sources.url` depends on the host invariant (LUM-773). Pre-LUM-773, a stale url may under-allowlist if it diverges from the payload — bounded until LUM-773 enforces alignment.
* A shared url parser is deduplicated across `_smtp_host_required`, `paperless_overlap._mailbox_host_port`, and the new helper (LUM-773 extends the same module).

**Future chunks must know**
* The mailbox host anchor is `sources.url` (with LUM-773). Do not reintroduce a credential-payload host as canonical.
* The paperless mailbox-username decrypt is tracked separately (sibling), not by this ADR.
* The SMTP "Other" explicit-host still reads its payload — a future audit wanting full SMTP decrypt-free must revisit deliberately.

## Revisit conditions

* If a host must ever be enumerated for a credential row that has **no** source row (`instance` decoupled from `sources.id`), revisit the plaintext-column option.
* If LUM-773's invariant is dropped or weakened, revisit whether the reconciling fallback must become permanent.
* If internationalised-domain hosts are accepted in `sources.url`, revisit host normalisation (IDNA/punycode) in the shared parser.

## Status history

- 2026-08-02: Draft created by /explore LUM-634 (headless)
- 2026-08-02: Revised during /review-plan --arbitrate R1 — `declared_imap_hosts` decrypt only on url-miss; unconditional divergence-reconcile decrypt removed (defers to LUM-773 invariant)
- 2026-08-02: Finalised by /verify-plan — implementation confirmed decision
