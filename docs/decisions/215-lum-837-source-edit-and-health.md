# ADR-215 — `PATCH /sources/{id}` is the general source-edit route, and every poller records health

**Status:** Accepted
**Date:** 2026-08-18
**Ticket:** LUM-837 (chunk A)
**Supersedes:** nothing. **Extends:** LUM-628's mode toggle.

## Context

`PATCH /sources/{id}` shipped in LUM-628 as an IMAP mode toggle: one boolean,
one column, `422 mode_toggle_not_supported` for anything else. That made it the
only write path onto a source row short of delete-and-recreate — so **no source
of any type could have its name, category or poll interval changed**, and the
gap was invisible because `POST /sources` had no web client either, so nobody
had a screen from which to want it.

Building that screen (LUM-837) surfaced three further facts, none of which were
design decisions anyone had made:

1. **`unschedule_source` had exactly one call site in the codebase**, inside
   `delete_source`'s `if is_imap:` branch — while its own docstring described it
   as the reason a deleted source stops polling. Deleting a paperless, RSS or
   page source removed the row and left an APScheduler job holding a
   `SourceConfig` snapshot, polling the deleted source's URL indefinitely.
2. **Only the IMAP pollers recorded health.** `_poll_paperless_source` and
   `_poll_signal_source` advanced `last_polled_at` on every tick and wrote no
   `last_poll_error_code`, so a paperless source with no credential — a
   reachable steady state, since `POST /sources` never required one — read as
   freshly polled forever to any consumer of those columns.
3. **The `poll_interval` floor lived only in the scheduler.** `max(60, …)` in
   `schedule_source` meant the route accepted and stored values nothing
   honoured, and even the scheduler's own log line printed the unclamped number.

## Decision

**Widen the existing route rather than add a sibling.** `SourceModeUpdate`
gains `name`, `category` and `poll_interval`, applying to every `source_type`;
`full_ingest` stays IMAP-only and its `422` narrows to that field alone.
Anything wanting to change a source field extends this route from here on.

**An empty body stays `422`.** `status: "unchanged"` means *the values you
supplied already match* — which an empty body cannot be. A request naming no
field is a client bug and the shipped route already said so
(`test_missing_full_ingest_returns_422`). Enforced by a `model_validator`
because Pydantic cannot express "at least one of" natively.

**`full_ingest_active` is `null`, not `false`, when mode was not supplied.**
`false` already carries a meaning — "requested, and inert because
`EMAIL_FULL_INGEST_ENABLED` is off" — so a request that never mentioned mode
must not assert anything about it.

**`url` is not editable.** Changing it is indistinguishable from
delete-and-recreate: the poll cursor, the detected `source_type` and the
extraction method all derive from the original, and `signals.source_id` is a
plain TEXT column with no FK, so history would silently re-attribute. For
paperless it would also be close to decorative — the poller reads the
*credential's* `base_url`, not `sources.url`.

**The `SET` clause is built from a fixed column allowlist**
(`_SOURCE_UPDATE_COLUMNS`) with every value parameterised. No request key
reaches the SQL text; a test asserts the negative.

**Health is recorded for every source type**, not just IMAP:
`credential` / `auth` for paperless, `poll_failed` for RSS/page, cleared on a
successful poll. **`last_polled_at` is not health** — every poll path advances
it, including the ones that failed — and any surface rendering these columns
must treat the error code as the discriminator.

**`unschedule_source` runs for every source type on delete.** The IMAP-only
teardown (cleanup jobs, organize cache, IMAP/SMTP credentials) stays behind its
guard, because those are genuinely mailbox concerns.

**`POST` and `PATCH` gain `require_same_origin`,** matching `DELETE`. The
asymmetry was harmless while no browser called them; this chunk gives both a
browser client, which is exactly when it stops being harmless.

## Consequences

**A paused source outranks a failing one in any health display.**
`_record_poll_health` clears an error only on a *successful* poll, and a paused
source never polls again — so a stale error code persists indefinitely, and a
surface checking the error first would leave a paused row raising an alert
forever, making "pause it to silence it" impossible. Recorded here because it is
a property of the health model, not of one view.

**A `PATCH` racing a `DELETE` is a `404`.** The UPDATE uses `RETURNING id`; a
row deleted between the SELECT and the write reports not-found rather than
`"updated"`, and — importantly — does not reschedule a poll job for a row that
no longer exists, which would have re-created the orphan the delete fix removes.

**Narrowing `SourceRequest.poll_interval` to `ge=60` is a contract change** on a
shipped route, taken deliberately: it has never had a client, so nothing can be
relying on sub-60 being accepted, and this is the change that starts rendering
the value back to a user.

**Two source-creation postures still coexist and this does not resolve them.**
`POST /sources` is `require_admin` while `GET`/`PATCH`/`DELETE` are per-user and
owner-scoped — a create-only anomaly, almost certainly historical. LUM-839 owns
moving it; nothing in the shipped view hard-codes "admin" in copy or logic, so
that should be a remount rather than a rewrite.

**Not built here:** adding a source from the web, and the two paperless consent
gates on the create route (LUM-729's starvation arm and LUM-835's
duplicate-filing arm), which remain reachable only by API. That is chunk B.

## Alternatives considered

- **A second, general edit route beside the mode toggle.** Rejected: two write
  paths onto one table diverge, and the mode toggle is a special case of a
  partial update rather than a different operation.
- **Making an empty body a no-op `"unchanged"`.** Proposed in the plan and
  reversed in review — it would have inverted a shipped test and given
  `"unchanged"` two meanings.
- **Filing the poller health gap as a follow-up rather than fixing it here.**
  The plan originally did exactly this and had the view "work around it". The
  workaround was a branch that could never execute, with a test that seeded an
  error code existing nowhere in production — so the view shipped green while
  asserting health it could not know. Fixing the poller was cheaper than
  shipping that.

## Status history

- 2026-08-18: Accepted, from LUM-837 chunk A's `/verify-plan`. Chunk B (the add
  flow and both consent arms) is planned and unbuilt; this ADR covers the edit
  route, the health model and the delete fix only.
- 2026-08-22: Chunk B shipped (`AddSourcePanel` at `/admin/sources`; web tests
  `AdminSourcesConsent.test.tsx`). Status history above is historical — the add
  flow and both consent gates are now built.
- 2026-09-02: The household add surface is `/me/sources` (**LUM-839** / **ADR 217**);
  LUM-842 Playwright locks both consent flags. The 2026-08-22 note's `/admin/sources`
  path is historical.
