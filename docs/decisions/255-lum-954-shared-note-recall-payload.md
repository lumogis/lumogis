# ADR-255: One payload contract for the `conversations` collection (shared note recall, LUM-954)

**Status:** Finalised
**Created:** 2026-11-11
**Last updated:** 2026-11-11
**Decided by:** claude-opus-5-5 (/explore --headless LUM-954)
**Finalised by:** /verify-plan 2026-11-11 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-954-shared-note-recall-payload.plan.md`
**Exploration:** `.cursor/explorations/LUM-954-shared-note-recall-payload.md`
**Draft mirror:** `.cursor/adrs/LUM-954-shared-note-recall-payload.md`
**Relates:** [ADR 015](015-personal-shared-system-memory-scopes.md) (memory scopes; unchanged)

## Context

Session recall (`orchestrator/services/memory.py` `retrieve_context`) searches the Qdrant `conversations` collection and reads `session_id` and `summary` from each hit. It feeds chat context injection, MCP `memory.search` and MCP `context.build`. Five writers put points into that collection. Personal sessions, personal Web-capture notes and session projections wrote `session_id`/`summary`. The household projections of notes (`{note_id, text}`) and audio memos (`{audio_id, transcript}`) did not. So a note shared with the household passed the visibility filter and came back from recall with empty text. In chat it also took one of the three memory slots and contributed nothing. Found by the LUM-36 live scope tests (LUM-954).

Unpublish and purge delete projection points by deterministic point id, and the visibility filter keys on `scope` and `user_id`. None of them depends on the other payload keys, so the payload shape was free to change.

## Decision

Every writer to `conversations` builds its payload through one helper, `services.memory.conversation_payload(*, item_id, summary, user_id, scope, **extra)`. The reader-facing contract is `session_id` = the item's id and `summary` = its recallable text. Kind-specific keys (`note_id`, `audio_id`, `published_from`, `source`, `topics`, `entities`) are extras. The helper rejects an empty `item_id`, a `scope` outside `personal` / `shared` / `system` (no arm of `visible_qdrant_filter` admits any other scope, so such a point would silently never recall), and an `extra` that overrides a contract key. Note and audio-memo projections therefore take the personal-capture shape plus `published_from`; `text` and `transcript` are no longer written to Qdrant (Postgres keeps the canonical copy).

`retrieve_context` keeps a narrow, commented fallback (`summary` → `text` → `transcript`, `session_id` → `note_id` → `audio_id`) so points written before the fix read correctly without a backfill. It runs only on hits Qdrant has already admitted through the visibility filter, so it changes how a hit is rendered, never which hits return. Republishing rewrites such a point in the canonical shape.

## Alternatives Considered

- **Map payloads in the reader only:** cheapest, but leaves three shapes in one collection and every later writer free to invent a fourth.
- **Writer helper with no legacy handling:** leaves notes that are already shared blank in recall, and nothing tells their owners to republish.
- **Writer helper plus a re-projection backfill job:** a module, an operator step and a graph re-queue for a population that is likely empty (Web has no note publish control).
- **Separate `notes` collection:** reverses the Phase 5G choice to keep notes beside sessions, and touches about ten files.
- **`set_payload` batch backfill:** the `VectorStore` port has no `set_payload` or filtered scroll; not worth new port methods for a one-off.

Full detail: `.cursor/explorations/LUM-954-shared-note-recall-payload.md`.

## Consequences

- Shared and system notes and audio memos are recalled with their text by members allowed to see them. Members with `allows_shared=false` still see nothing, because visibility is unchanged and enforced in Qdrant before payload mapping.
- A new kind that writes to `conversations` (e.g. LUM-594 transcript sharing) gets the contract by calling the helper. A source tripwire (`test_every_conversations_writer_uses_the_helper`, `ast`-based) fails when a module writes to `conversations` with a literal collection name without calling the helper, or when the set of writer modules changes.
- Admin restore and per-user import re-upsert stored payloads through a variable collection name and are not matched by the tripwire; they write back whatever shape was backed up.
- `text`/`transcript` no longer duplicate the note body in the Qdrant payload.
- The reader carries a small legacy fallback until it is removed deliberately.
- Restore and import paths could later re-embed `conversations` points from `summary` uniformly (today they write zero vectors; LUM-754 Later).

## Implementation notes

- `orchestrator/services/memory.py`: `conversation_payload`, `_CONVERSATION_CONTRACT_KEYS`, `_CONVERSATION_SCOPES`; `store_session` uses the helper; `retrieve_context` maps hits through `_context_hit` with the `# LUM-954:` fallback.
- `orchestrator/services/projection.py`: `project_note`, `project_audio_memo`, `project_session` use the helper. `project_note` also writes `source` (same default as its Postgres INSERT).
- `orchestrator/services/captures.py`: `index_capture` uses the helper (keys unchanged).
- Tests: `orchestrator/tests/test_conversation_payload_contract.py` (helper, every writer against the reader, opted-out member, tripwire), legacy-shape cases in `orchestrator/tests/test_memory.py`, and two live cases in `orchestrator/tests/integration/test_memory_scope_isolation_live.py` (red before the fix on tier 2: Bob's recall returned `{'': ''}`).

**Implementation deviated (removal condition of the fallback):** the draft said to remove the fallback once a release had shipped in which every shared note was republished, or once restore re-embedded `conversations` points from `summary`. Implementation found that admin restore and user import re-upsert stored payloads verbatim, old keys included, so re-embedding alone would not clear old-shape points. The code comment now says: keep the fallback while a pre-LUM-954 backup can still be restored; remove it once no supported backup predates the fix. Intent (remove when no old-shape point can exist) is unchanged.

## Revisit conditions

- A reader needs to tell sessions, notes and transcripts apart (e.g. different rendering for LUM-594). Then add a `kind` discriminator through the helper.
- No supported backup predates LUM-954, or restore/import rewrite payloads into the canonical shape. Then remove the `retrieve_context` legacy fallback.
- Recall moves to another collection or to the TEMPR fusion path (LUM-479). Then the contract moves with it.

## Status history

- 2026-11-11: Draft created by /explore
- 2026-11-11: Revised by /verify-plan — implementation revealed that restore/import re-upsert old payload keys verbatim, so the fallback's removal condition is tied to backups, not to republishing
- 2026-11-11: Finalised by /verify-plan — implementation confirmed the decision (ADR-255)
