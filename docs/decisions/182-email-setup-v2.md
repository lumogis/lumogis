# ADR-182: Email Setup v2 — guided provider auth, live connection test, import scopes, folder-derived labels

**Status:** Finalised
**Created:** 2026-07-22
**Last updated:** 2026-07-22
**Decided by:** /explore (claude-sonnet-5)
**Finalised copy:** docs/decisions/182-email-setup-v2.md

## Context

Connecting a mailbox is the *first* thing a user does in the LUM-641 email arc,
and today it is the least trustworthy step: `EmailMailboxSetupForm.tsx` is a bare
host/username/password form with a hard-coded `imap.gmail.com`/993/SSL/app-password
shape, **no connection test** before save, no provider guidance, no control over how
much history is imported, and a taxonomy seeded from a fixed 6-slug default with no
relationship to the user's real folders. Setup v2 makes the on-ramp legible and safe,
per the 7 product decisions already fixed on LUM-644.

The genuinely open architectural fork was **how the provider picker gets its
settings** — a static in-repo table versus an external autoconfig lookup (Mozilla
ISPDB over HTTP, or RFC 6186 DNS SRV). Everything else is mechanism design on the
existing `imapclient` stack: a live pre-save connection test, folder detection,
message counting, and a date-windowed import scope — none of which exist yet (every
adapter path hard-codes `select_folder("INBOX", readonly=True)` and polling is
UID-watermark only, never date-windowed).

Hard constraints in play: local-first (no domain may leak to a third party at
setup), the `imap_egress_scope`/SSRF/TLS guards LUM-625 exists to contain (any new
connect path must ride the *same* guards), and the app-password auth model the
credential layer enforces (`auth_type="app_password"` only; `xoauth2` reserved but
rejected).

## Decision

Adopt **Option 1 — a static in-repo provider registry** (a small AGPL data module
in `lumogis-core` mapping each supported provider → `{imap_host, imap_port,
smtp_host, app_password_url, help_steps[], notes}`), with **manual "Other" host
entry** for the long tail. The web picker renders provider cards from this registry
(exposed via a thin `GET /me/email-providers` route so LUM-650's SMTP prefill can
read the same source of truth); selecting a provider prefills host/port and links
its app-password page. No network lookup at setup time — nothing leaves the box
until the user's own IMAP connect.

Around that registry, add the missing read-only mechanism on the existing
`imapclient` stack: a **`probe()`** capability (RFC 6154 SPECIAL-USE folder
detection + INBOX message count + capabilities, read-only, inside
`imap_egress_scope`), a pre-save **`POST /me/email-sources/test`** route that takes
credentials transiently and **never persists them**, **folder-derived label seeding**
via the existing sparse-overlay `put_taxonomy` (seed-on-save, user confirms before
any row is written), and a **date-windowed import scope** (`SINCE`-based, stored on
the `sources` row) resolved to a starting UID at first poll.

**Outlook ships shown-but-disabled**: Microsoft retired Basic Auth / app passwords
for Outlook.com & Office365 IMAP/SMTP (enforcement completed 30 Apr 2026), so the
app-password model cannot connect Outlook. The v1 picker is Gmail / iCloud /
Fastmail / Other (manual), with Outlook visible-but-disabled and a "requires OAuth —
coming later" note.

## Alternatives Considered

- **Option 2 — Mozilla ISPDB autoconfig (HTTP lookup):** broader provider coverage
  than needed, bought at the cost of **leaking the user's email domain** to
  `autoconfig.thunderbird.net` on every setup and adding an outbound HTTP egress
  dependency that directly fights LUM-625's IMAP-egress containment. Wrong trade for
  a local-first product. See exploration for detail.
- **Option 3 — RFC 6186 DNS SRV lookup:** less leaky than HTTP (DNS only) but still an
  external lookup, and **coverage is poor** for our named providers (Gmail publishes
  no usable SRV) so it degrades to manual anyway. Buys little over the static table.
- **OAuth2 provider connect (Gmail/Microsoft/Apple):** the "right" long-term auth and
  the only path that unlocks Outlook, but a large separate chunk (app registration,
  redirect handling, token storage/refresh, `auth_type="xoauth2"` which the credential
  layer reserves-but-rejects today) — out of scope for Setup v2; a dedicated future
  child of LUM-641.
- **Full IMAP autodiscovery cascade (`autoconfig.{domain}` → ISPDB → SRV → guess):**
  Thunderbird-grade discovery, far past the scope's fixed picker, dragging in the
  egress/privacy costs of Options 2–3.

Full comparison: `.cursor/explorations/LUM-644-email_setup_v2.md`.

## Consequences

**Easier:**
- Guided, verifiable setup with zero domain leakage and no new egress host — the
  self-hoster gets provider guidance without phoning any third party.
- The static registry becomes the single source of provider host/URL truth; LUM-650
  extends it (adds per-provider SMTP host) rather than forking its own table — the
  connection-test seam and registry are reusable for SMTP submission setup.
- The risky part (a live connect during setup) reuses the *same*
  `imap_egress_scope`/SSRF/TLS guards the poll path already goes through — no new
  attack surface, no new dependency, no new Docker service.
- Folder = ground truth for already-foldered mail (no LLM); the classifier only runs
  on unsorted/incoming, reusing the existing sparse-overlay taxonomy write path.

**Harder / foreclosed:**
- Commits to **app-password IMAP** as the v1 auth. **Outlook cannot be a first-class
  provider** until an OAuth2 chunk lands; it ships visible-but-disabled.
- Provider coverage is limited to the table we maintain (hosts/URLs drift
  occasionally — a table edit); unlisted providers use manual "Other" entry, with no
  "type your email and we'll figure it out" magic.
- Full-inbox import (empty-cursor `1:*` drain) stays flagged dangerous and is likely
  **deferred to a follow-up** (background job + progress + cloud-classifier warning);
  v1 ships the `SINCE`-based scopes (Just-new / Last-week / Last-30-days / Custom
  range).

**Future chunks must know:**
- LUM-650 (SMTP send) reads provider hosts from this registry and reuses the
  connection-test seam — do not build a parallel SMTP host table.
- An import-scope column is added to `sources` (small additive migration); first-poll
  seeding resolves the scope to a starting UID via `SINCE` search.

## Revisit conditions

- **If an OAuth2 provider-connect chunk lands**, revisit the picker to promote Outlook
  to a first-class (OAuth) provider and reconsider whether Gmail/iCloud/Fastmail
  should prefer OAuth over app passwords.
- **If Microsoft (or another major provider) re-enables app passwords**, revisit the
  Outlook disabled state.
- **If the maintained provider table proves burdensome to keep current** (hosts/URLs
  drifting faster than expected), revisit whether a *cached, opt-in* ISPDB lookup with
  an explicit egress allowlist entry earns its keep — but only with the domain-leak
  trade made explicit to the user.
- **If per-provider folder-flag variance** (RFC 6154 SPECIAL-USE) turns out wider than
  the live `probe()` handles, revisit the folder→role mapping.

## Status history
- 2026-07-22: Draft created by /explore (claude-sonnet-5)
- 2026-07-22: Revised during /review-plan --arbitrate R1 (Fable-5 critique). Recorded a
  v1 scope narrowing: the historical **backfill reads INBOX only** (matching all
  existing polling); the decision-5A "folder = ground truth for already-foldered mail
  (no LLM); classify only unsorted/incoming" is **deferred to LUM-646** (chunked
  cleanup). Also corrected the backfill dedup mechanism to the existing URL-based
  `_is_duplicate` (email `signal_id` is empty; no `ON CONFLICT (signal_id)` dedup
  exists), and pinned the live-cursor/backfill-cursor split with a seed-once guard.
- 2026-07-22: Revised during /review-plan --arbitrate R2 (Sonnet-5 critique, which
  found two bugs inside R1's fixes). (1) **Backfill completion** is now
  *empty-search-result*, not `backfill_cursor >= backfill_max_uid` — the latter never
  terminated when out-of-window mail sat above the window (the exact chunked-cleanup
  case). (2) **The pre-save live test is a Layer-1 feature**: on Layer-2-proxied
  deployments it soft-Saves (verify-on-first-poll) rather than testing, because the
  Squid allow file is host-regenerated and unreachable from the in-container runtime
  (a discovered pre-existing LUM-625 path defect, filed separately). Max-UID reads use
  `UIDNEXT − 1` (not the MESSAGES count).
- 2026-07-22: Finalised by /verify-plan — implementation confirmed the decision.
  Implemented as specified across backend (migration 059, provider registry, probe,
  test route, import-scope state machine) + web guided form. One code-review P0 fixed
  during verification: `last_7d`/`last_30d` store `import_since=NULL`, so the driver
  now computes the SINCE window (today−7d/30d) at seed time. Finalised copy at
  docs/decisions/182-email-setup-v2.md.
