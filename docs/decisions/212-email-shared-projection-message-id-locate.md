# ADR 212: Resolve Message-ID through shared projection rows
**Status:** Finalised
**Created:** 2026-08-10
**Last updated:** 2026-08-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-08-10 (Cursor Grok 4.5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/email_shared_projection_message_id_locate_retro.md`
**Draft mirror:** `.cursor/adrs/email_shared_projection_message_id_locate.md`

## Context

Household email cards can show a **shared projection** of the owner's own mail after `collapseProjections` (LUM-661). Projection INSERTs omit `email_message_id` — that column lives on the personal original only. LUM-810's body card already passed `published_from ?? signal_id` so `/body` worked. Quote / reply / reply-all / forward still keyed on the projection id, so after mail left INBOX the locate path fell back to a blind UID fetch and returned empty quotes despite a working body.

LUM-813 had unified two disagreeing `_message_id_for` copies into `services/email_message_locate.py`. A Cursor bugfix that walked `published_from` never merged; three re-applies followed (`fb65` → `8c30` → `2fb4`). Merged to `dev` as `feab4e448` / `9fe0edf22` via `/review-cursor-branches` on 2026-08-10.

## Decision

**IMAP locate always resolves through the personal original when the signal is a shared projection.**

1. `email_message_locate._lookup_ids` returns `[signal_id, published_from]` (one hop) when `published_from` is set and distinct.
2. `message_id_for` and `organize_folder_for` iterate that list before organize-log / give-up.
3. Lumogis Web `EmailInboxView` uses the same hop (`locateSignalId`) for composer / reply-all recipient routes — defence in depth with the server.
4. On the LUM-814 text-only fetch path, accumulated part bytes must not exceed `_MAX_BODY_BYTES` even if BODYSTRUCTURE sizes lied.

Do **not** reintroduce a second Message-ID helper beside `email_message_locate`.

### As-implemented surface

- `orchestrator/services/email_message_locate.py` — `_lookup_ids`, updated `message_id_for` / `organize_folder_for`
- `clients/lumogis-web/src/features/email/EmailInboxView.tsx` — `locateSignalId`
- `orchestrator/adapters/email_imap_source.py` — post-fetch size guard on `_fetch_text_only`
- `orchestrator/tests/test_email_message_locate.py` — projection hop regressions

## Alternatives considered

- **Copy `email_message_id` onto every projection INSERT** — would fix locate without a hop, but needs migration/backfill and duplicates mailbox identity; not chosen at ship time.
- **Client-only hop** — rejected; non-web and stale clients would still miss the Message-ID.
- **Leave quote broken when body works** — rejected; user-visible inconsistency after LUM-810.

## Consequences

- Quote/reply/forward locate matches body-read behaviour for collapsed owner projections.
- Future callers of `message_id_for` inherit the hop automatically.
- Nested projections (projection-of-projection) are out of scope; revisit if product ever creates them.

## Revisit conditions

- Projection rows begin storing `email_message_id` and historical rows are backfilled → hop may become optional.
- Evidence of multi-hop `published_from` chains → extend `_lookup_ids` or forbid nesting.
- Live IMAP shows the post-fetch size refuse is too strict → adjust with fixtures; keep a hard cap.

## Linear linkage (Product OS)

- **Existing issue:** none for this defect. Related history: **LUM-813** (Done — unify locate helpers), **LUM-810** / **ADR-207**, **LUM-661**, **LUM-814** / **ADR-208**.
- **New issue needed:** yes — Product OS backfill for as-shipped closure (see `/record-retro` chat **`### Linear backfill required`**).
- Skills do **not** call the Linear API.

## Testing retrospective

- **Added/changed:** `orchestrator/tests/test_email_message_locate.py` (fake `published_from` + hop cases).
- **Run:** `AUTH_ENABLED=false pytest -q tests/test_email_message_locate.py` on tip and on merged `dev`.
- **Results:** **12 passed**.
- **Gaps:** no live IMAP / Playwright proof on a collapsed projection after an external move (hardware gate remains with the LUM-811 family).
- **`docs/testing/automated-test-strategy.md`:** no change.
- **Release skills:** no change.

## Status history

- 2026-08-10: Finalised by /record-retro (retrospective). Shipped in `feab4e448`; merge `9fe0edf22` on `dev`.
