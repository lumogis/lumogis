# ADR 085: Amendment to ADR 074 — conversation PUT upsert and purge tombstone guards (LUM-439)

**Status:** Finalised
**Created:** 2026-06-06
**Last updated:** 2026-06-06
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-06 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this bugfix
**Exploration:** `.cursor/explorations/lum_439_conversation_put_upsert_fix_retro.md`
**Draft mirror:** `.cursor/adrs/lum_439_conversation_put_upsert_fix.md`
**Amends:** `docs/decisions/074-lum-162-conversation-history-ui.md` (slice-2 `PUT` / transcript sync semantics only)

## Context

**0.7.0** shipped LUM-162 conversation history including slice-2 transcript sync (`web_conversations` / `web_messages`, migration **027**). Post-release, `PUT /api/v1/conversations/{id}` routed to `update_web_conversation`, which **UPDATE**d an existing `web_conversations` row and built its response via `get_conversation` (expecting a `sessions` row). Client-minted thread IDs never created the header row, so debounced `POST .../messages` returned **404** and verbatim transcripts were **silently dropped** — a **data-loss** class bug (`risk:data-loss`).

Fix landed on `dev` as `22777bf7033463917328740b71735ed89df6facb` under historical LUM-162 messaging; tracked retrospectively as **LUM-439** (child of LUM-162).

## Decision

1. **`PUT /api/v1/conversations/{id}` upserts** the slice-2 header: `update_web_conversation` delegates to `upsert_web_conversation` (`INSERT … ON CONFLICT DO UPDATE` on `web_conversations`) and returns `_web_conversation_summary` (sessions row optional for summary text).
2. **Purge tombstones block resurrection:** `upsert_web_conversation` and `append_web_message` call `is_conversation_purged`; when true, raise `ConversationNotFoundError` (HTTP **404**) — purged conversations stay purged.
3. **Regression tests** in `orchestrator/tests/test_api_v1_conversations.py`: `test_put_upserts_web_header_before_session_row`, `test_put_then_append_message_persists_transcript`, `test_put_and_append_blocked_after_purge_tombstone`.

ADR **074** remains the canonical record for the conversation-history programme; this ADR **narrows** slice-2 write semantics for `PUT` and tombstone interaction only.

## Alternatives considered

- **Keep UPDATE-only `PUT`** — rejected; breaks client-minted thread sync (shipped bug in 0.7.0).
- **Auto-create header only in `POST .../messages`** — rejected; web client already issues best-effort `PUT` on mount; upsert on `PUT` matches client contract and fails fast on tombstones.
- **Separate bugfix ADR number without amending 074** — rejected for reader clarity; explicit **Amends ADR 074** link preferred over silent drift.

## Consequences

- **Easier:** slice-2 persistence works for active tabs; private `main` / public export can promote the fix with documented closure.
- **Harder:** operators on 0.7.0 without this patch still lose transcripts until upgraded.

## Testing retrospective

| Layer | Command / artefact | Result |
| --- | --- | --- |
| API unit | `test_put_upserts_*` (3 cases) | Green on `dev` |
| Compose-test | `make verify-public-rc-full` → compose-test | **2028 passed**, 25 skipped |
| Playwright | `chat-conversation-history.spec.ts` reload (slice-2) | **15 passed**, 2 skipped (RC gate) |
| Log | `/tmp/verify-public-rc-full-rc-post-v7.log` on RC `1caf437f1` | **PASSED** |

**P0 gaps:** none for LUM-439 scope.

## Revisit conditions

- Reopen if `PUT` field-merge semantics change beyond title/model.
- Reconcile with LUM-414 when Playwright list/delete coverage closes (orthogonal to upsert fix).

## Linear linkage (Product OS)

- **LUM-439** — bug ticket; closed **Done** with this retro evidence.
- **LUM-162** — parent feature (Done); this is post-closure repair, not a reopen.
- **LUM-414** — Playwright P1 follow-up; separate.

## Status history

- 2026-06-06: Finalised by /record-retro (retrospective as-built; amends ADR 074 slice-2 PUT behaviour).
