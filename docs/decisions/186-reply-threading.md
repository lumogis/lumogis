# ADR-186: Reply threading — RFC-correct In-Reply-To / References via IMAP re-fetch
**Status:** Finalised
**Created:** 2026-07-23
**Last updated:** 2026-07-23
**Decided by:** /explore (claude-opus-4-8)
**Finalised copy:** docs/decisions/186-reply-threading.md

## Context
LUM-650 shipped the outbound send path with a MIME builder (`SmtpSender.build_message`) deliberately made **threading-ready** (it accepts `in_reply_to`/`references`), and the reply handler already re-fetches the original **Subject + Message-ID** over IMAP and sets `In-Reply-To`. But the reply's **`References` chain is still empty**, so replies don't nest in clients that thread by `References` (Gmail, Apple Mail, Thunderbird, Outlook). The gap is concrete: `adapters/email_imap_source.py::_HEADER_FIELDS` does not include `References`/`In-Reply-To`, so the existing re-fetch can't see them. Stored signals persist neither header, so only a live re-fetch has the truth. This decision is about the **mechanism**: where the extra headers are fetched and how the chain is constructed.

## Decision
Complete reply threading with a **dedicated reply-header re-fetch** (Option B), not a wider ingest fetch and not ingest-time persistence:

- Extend the reply path's IMAP re-fetch (`smtp_send._refetch_original`, folding into a reply-specific `BODY.PEEK[HEADER.FIELDS (REFERENCES IN-REPLY-TO MESSAGE-ID SUBJECT)]` read — one connection, read-only, inside `imap_egress_scope`) so it returns the original's `References` + `In-Reply-To` + `Message-ID` + `Subject`. The ingest poll's lean `_HEADER_FIELDS` (and the LUM-657 sender backfill) stay untouched.
- Construct the reply's headers per **RFC 5322 §3.6.4**: `In-Reply-To` = parent `Message-ID`; `References` = parent `References` (or parent `In-Reply-To` when it has no `References`) + parent `Message-ID`. Pass `references=` to the already-threading-ready `build_message`.
- Keep LUM-650's graceful degradation: a moved/trashed original (re-fetch miss) sends with neither `In-Reply-To` nor `References`, never a hard failure. Compose-new carries no thread headers.
- No new dependency, no migration, no new Docker service or env var.

## Alternatives Considered
- **Widen the shared `_HEADER_FIELDS`** — one-liner, but adds unused `References` bytes to every ingest poll + the LUM-657 backfill; wrong altitude (couples ingest headers with reply-threading headers). See exploration.
- **Persist `message_id`/`references` columns on `signals` at ingest** — a migration + backfill + unbounded per-row storage for data cheaply re-fetched on demand; the ticket's own "persist only if a need emerges" defers it.
- **In-Reply-To only / synthesize from local data** — fails the ticket (clients thread on `References`) or lacks the authoritative chain.

Full detail: `.cursor/explorations/LUM-651-reply-threading.md`.

## Consequences
**Easier:**
- Replies nest correctly in the recipient's client — the visible payoff of the send path.
- The `build_message(references=…)` contract is exercised end-to-end, so any future sender (LUM-654 agentic, an autonomous sender) inherits correct threading for free.
- Zero cost to the ingest hot path and the sender backfill; reuses the shipped read-only/egress-scoped/graceful-fallback re-fetch pattern.

**Harder / foreclosed:**
- Commits to **re-fetch-at-send** for threading rather than ingest-time persistence — a deliberate, revisitable choice.
- Threading quality depends on the original still being fetchable; a moved/trashed original degrades to an unthreaded (but delivered) reply — accepted, consistent with LUM-650.

**Future chunks must know:**
- LUM-652's Send UX is unaffected — threading is internal to the reply MIME the send action builds.
- The `References` truncation strategy (if adopted) is the only place a very long thread is lossy; document it where the header is built.

## Revisit conditions
- **Re-fetch latency or miss-rate becomes a real problem at scale** → revisit Option C (persist `message_id`/`references` on `signals` at ingest + a backfill).
- **A supported client mis-threads despite RFC-correct headers** → revisit the construction/truncation strategy against that client's observed behaviour.
- **An autonomous/bulk sender lands** → confirm it routes through the same `build_message`/`_build_references` path so threading is not re-implemented.

## Status history
- 2026-07-23: Draft created by /explore (LUM-651).
- 2026-07-23: Finalised by /verify-plan — implementation confirmed the decision (dedicated read-only `fetch_thread_headers`; RFC 5322 §3.6.4 construction; jwz-style truncation; re-fetch-at-send, no persistence). Refinements recorded from review: the structural defense is **selective field-copy** (only 4 named headers read + reconstructed); `_build_references` **whitespace-normalizes AND strips non-whitespace control chars** so a hostile original degrades (inert tokens) rather than dead-lettering via `build_message`'s CR/LF backstop; the ingest `_HEADER_FIELDS` + LUM-657 backfill stay untouched (shared `_parse_header_fields` helper). Finalised copy at docs/decisions/186-reply-threading.md.
