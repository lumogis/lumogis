# ADR-174: Email IMAP signal source — IMAPClient, in-process, auto-classification of incoming mail (LUM-170)

**Status:** Finalised (with deviations recorded)
**Created:** 2026-07-17
**Last updated:** 2026-07-17
**Decided by:** /explore (Opus 4.8) → finalised by /verify-plan
**Finalised copy:** `docs/decisions/174-email-imap-signal-source.md`

## Context

Email is the highest-value household-admin signal source and the `read` foundation of the email epic (LUM-513: read → ingest → act). It must be **read-only, local-first, and privacy-preserving** (metadata-only by default; opt-in full-ingest processed by a **local** LLM/spaCy — cloud LLM never on the body). The 2025–26 auth landscape forced the design: Gmail (Mar–May 2025) and Microsoft 365 (Sept 2024) disabled Basic Auth for third-party IMAP, and Google's full-IMAP scope is now audit-gated (CASA Tier-2, $540–$1,000) for public apps. The repo already has the exact scaffolding to reuse: the CalDAV connector, the `feed_monitor` signal, the ADR-018 encrypted credential store, APScheduler, and the local ingest pipeline.

## Decision

Build the email connector as an **in-process signal source using IMAPClient** (BSD-3, actively maintained — v3.1.0 Jan 2026; structured responses, UID-native, IMAP IDLE, OAuth2/XOAUTH2/OAUTHBEARER). **Revised during /create-plan after codebase scan:** rather than a standalone `orchestrator/signals/email_monitor.py`, email plugs into the **existing `feed_monitor` dispatch** as a new `source_type == "imap"` (an `EmailImapSource` adapter under `adapters/`), mirroring exactly how `caldav` (metadata → `process_signal`) and `paperless` (full-ingest, never `process_signal`) already coexist there — this inherits scheduling, dedup, poll-cursor, and the manual-sync hook for free. It adds a connector-credential wrapper (`orchestrator/services/email_credentials.py`) mirroring `caldav_credentials.py`, is scheduled by APScheduler, stores credentials in the existing ADR-018 store, and reuses `ingest.py`/`signal_processor.py` for the local full-ingest. **v1 auth default = app-password + generic IMAP** (universal, zero OAuth-verification friction); **XOAUTH2 = bring-your-own-OAuth-app opt-in**. Both **metadata signals and local full-ingest are in v1 scope** (sequenced/mergeable internally, not split to a later ticket), and the **primary feature is automated classification of incoming mail** — a hybrid rules + **local** LLM/spaCy pass emitting an importance/priority signal + category labels into the signal/KG store (cloud LLM never on the body). Fetch is **APScheduler poll plus a user-triggered manual "Sync now"** action. v1 classification is **read-only into Lumogis' own store** — mailbox write-back (labels/sort/draft) is the act tier (LUM-229), which reuses this connection layer. The declared mail host is the capability's LUM-618 `external_endpoints` (single-host containment). Scope: **core** (connector + metadata signals + classification); the KG-enrichment / relationship-health consumers stay **app** scope.

## Alternatives Considered

- **imaplib (stdlib)** — reimplements what IMAPClient provides (parsing, IDLE, OAuth) to avoid one small BSD dep; not worth it.
- **aioimaplib (async, GPLv3)** — async overkill at household scale (scheduled poll, few mailboxes); revisit only if IDLE-push across many mailboxes is required.
- **EmailEngine gateway** — ruled out: source-available + commercial (~$895/yr) → AGPL-incompatible for core, breaks the self-hoster promise, and adds a Docker service for what a BSD in-process library does.
- **Provider APIs (Gmail API / MS Graph)** — not universal and carry the same/worse OAuth-audit surface; a later per-provider push enhancement, not the base.

Full detail: `.cursor/explorations/LUM-170-email-imap-signal-source.md`.

## Consequences

**Easier:** the household's #1 admin channel becomes searchable/proactive ("did I hear from Mia's school?", commitment/deadline extraction) with a local-first, privacy-strengthening posture; one new signal + one credential wrapper on an existing pattern; a clean single-declared-host egress that showcases LUM-618 containment; app-password-first auth keeps v1 at S→M and dodges the CASA-audit cost.

**Harder / committed:** commits to **IMAP as the universal base** (provider-native push becomes a later additive layer); OAuth-app verification (CASA) is deferred as a business/ops decision, so a Lumogis-branded one-click Gmail/Outlook OAuth flow is *not* in v1 (operators use app passwords or BYO OAuth apps); the connection/credential layer must be built **reusable** because the act tier (LUM-229) will share it.

**Future chunks must know:** the connector-credential id + payload shape and the signal schema are the contract that LUM-152 (comms signal), LUM-110 (proactive pipes), and LUM-229 (email act tier) consume — keep them stable; full-ingest reuses the existing ingest pipeline (no parallel extraction stack).

## Revisit conditions

- **Real-time push becomes a requirement** across many mailboxes → revisit IMAP IDLE / `aioimaplib` or provider push (Gmail `history`, Graph subscriptions).
- **Lumogis ships a verified OAuth client** (CASA Tier-2 completed) → add a one-click Gmail/Outlook OAuth flow alongside app-passwords.
- **A provider drops IMAP entirely** (the 2026 free-tier-IMAP-restriction trend accelerates) → add that provider's native API as a per-provider adapter.
- **IMAPClient maintenance lapses** (>12 months no release) → re-evaluate vs `imaplib`/`aioimaplib`.

## Implementation deviations (recorded at finalisation)

The core decision held (IMAPClient in-process, feed_monitor `source_type=="imap"` dispatch, app-password-first, local-only classification). Three refinements emerged during implementation and review and are part of the record:

1. **Multi-mailbox (scope-up, user directive).** One IMAP account per user was too tight (households have personal + work + shared inboxes). The shared ADR-018 credential store was made **multi-instance** — an `instance` column (default `''`, PK `(user_id, connector, instance)`); IMAP keys credentials by `instance = sources.id`. Other connectors are byte-for-byte unaffected. Key rotation (`_reencrypt_user_table`) and the wire model carry `instance`.
2. **XOAUTH2 scoped OUT of v1.** The ADR framed XOAUTH2 as a BYO-app opt-in; implementation **rejects `auth_type="xoauth2"` at load** (v1 = app-password only) so the app-password-only adapter can't be handed a `None` password. The payload field stays reserved; unattended OAuth (refresh/expiry) is a follow-up.
3. **LUM-618 egress containment scoped OUT; SSRF guard instead.** LUM-618's egress proxy is HTTP-only (Squid) and IMAP hosts are per-user runtime data — it cannot carry IMAPS. Replaced with a dedicated `_validate_imap_host` SSRF guard (link-local/metadata always blocked; loopback/private gated) plus TLS enforcement. IMAP-aware containment is a follow-up. **→ CLOSED by LUM-625 / [ADR-178](178-lum-625-email-imap-egress-containment.md) (2026-07-20):** two honest layers — an in-process `tethered` allowlist around `_connect` (Layer 1, every deployment) plus a Docker-only Squid `CONNECT`-tunnel that allowlists mail hosts by SNI and closes the DNS-rebinding TOCTOU (Layer 2). The "HTTP-only" half of the original rationale was over-stated (a `CONNECT` tunnel carries IMAPS); the "per-user runtime data" half is addressed by a household-union allow file with static regen (a dynamic control plane remains deferred).

The privacy boundary is enforced on the **real** pipeline (`process_signal`/`score_importance` call sites), guarded by an on-box-local model check stricter than `config.is_local_model`.

## Status history
- 2026-07-17: Draft created by /explore (LUM-170). Recommendation: IMAPClient in-process, metadata-only-first, app-password-first auth; High confidence; small optional auth-matrix PoC advised.
- 2026-07-17: Revised during /create-plan (LUM-170) — (1) scope: both metadata + local full-ingest in v1 with **automated mail classification** (importance + fixed category taxonomy) as the core feature, read-only in v1; (2) placement: **feed_monitor dispatch (`source_type == "imap"`)** instead of a standalone `email_monitor.py`, after the codebase scan showed caldav/paperless already establish this seam.
- 2026-07-17: **Finalised by /verify-plan** — implementation confirmed the decision; three deviations recorded above (multi-mailbox multi-instance store, XOAUTH2 out of v1, LUM-618 containment → SSRF guard). Tests green (369 across the LUM-170 + regression sweep). Finalised copy at `docs/decisions/174-email-imap-signal-source.md`.
