# ADR-237: IMAP hosts are compared, stored and connected in IDNA2008 A-label form (LUM-782)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/imap_host_idna_normalization_retro.md`
**Draft mirror:** `.cursor/adrs/imap_host_idna_normalization.md`
**Linear:** LUM-782
**Amends:** ADR-200 (its IDNA revisit condition; the host compare is no longer bare `lower()`)

## Context

ADR-200 (LUM-773) made `sources.url` the canonical host for an instance-bound IMAP mailbox and compared it with the credential payload host using `lower()` only. Its revisit condition named internationalized hosts. `bücher.example` and `xn--bcher-kva.example` are the same host but compared unequal. Python's built-in codec is IDNA2003, which maps deviation characters to different names (`faß.example` → `fass.example`).

## Decision

`normalize_imap_host` (`orchestrator/services/imap_source_url.py`) gives the one canonical form: lower-case ASCII A-label, no trailing dot, or `None` when the host is invalid.

- Non-ASCII hosts go through IDNA2008 with UTS46 mapping (`idna` package). ASCII hosts are lower-cased and dot-stripped, with `xn--`, empty and over-long labels validated. IP literals are lower-cased.
- The LUM-773 compare uses it on both sides. An un-normalizable payload host fails as the existing `imap_host_mismatch`.
- `POST /me/email-sources` stores the A-label in `sources.url`; an invalid host is a 422 `bad_host`.
- **Every network use takes the normalized host:** the IMAP and SMTP connection builders normalize before resolve, the SSRF check, the TLS check and connect. The stored credential payload keeps what the client sent (ADR-200).
- `None` is always a failure; no caller falls back to the raw string.

## Alternatives considered

- **Store and compare the unicode form.** Not chosen: IMAP clients, DNS and TLS certificates use the A-label.
- **The stdlib IDNA2003 codec.** Not chosen: it maps `ß` and similar characters to a different host.
- **Normalize only the compare.** Not chosen: the socket would still resolve the raw payload string through IDNA2003, so an accepted credential could connect somewhere the source does not name.
- **Full IDNA validation of ASCII hosts.** Not chosen: it would break existing non-LDH hosts such as `my_host.lan`.
- **A migration for stored rows.** Not chosen: rows are normalized on read.

## Consequences

- All spellings of one host are the same host for comparison, storage and connection.
- An invalid host fails at registration or before resolve.
- `idna` is used directly but pinned only through `httpx`.

## Revisit conditions

- `httpx` stops pulling in `idna`, or `idna` changes major version: pin it.
- Strict LDH validation of ASCII hosts is wanted.
- A provider preset gains a non-ASCII host (OAuth device-flow URL).

## Linear linkage (Product OS)

- **Existing issue:** LUM-782
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

About 34 new cases across the helper, the compare, the route and the connection builders; 246 targeted tests pass; two mutation checks (compare and connect) killed. No live IMAP connect to an IDN host.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
