# ADR-105: LUM-162 session purge follow-ups (LUM-417, LUM-419)

**Status:** Finalised  
**Created:** 2026-06-18  
**Last updated:** 2026-06-18  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-06-18 (Composer)  
**Plan:** none — Claude Code web batch  
**Exploration:** `.cursor/explorations/lum_162_session_purge_followups_retro.md`  
**Draft mirror:** `.cursor/adrs/lum-162-session-purge-followups.md`  
**Parent:** ADR-074 (`074-lum-162-conversation-history-ui.md`)

## Context

LUM-162 verify-plan registered P2 follow-ups for session-end UX and graph projection purge. **LUM-417** and **LUM-419** shipped on the Claude batch without formal plan files.

## Decision

### LUM-417 — Pending-summary UX

- `ConversationSidebar.tsx` shows pending state when `POST /session/end` enqueues batch work before `sessions` row exists.
- Vitest coverage in `ConversationSidebar.test.tsx`.

### LUM-419 — Graph purge for published Session projection nodes

- `graph/writer.py` — `published_from` on projection Session nodes; `delete_session_projections(gs, source_session_id=...)`.
- `memory_purge.py` calls both personal `delete_session` and projection cleanup.
- Tests in `orchestrator/tests/premium/test_graph_delete_session.py`.

## Alternatives considered

- Blocking sidebar on sessions row — rejected (false broken-history perception).
- Deleting projection nodes only by `conversation_id` — rejected (publish creates distinct lumogis_ids).

## Consequences

- Household publish + delete no longer leaves orphan Session projection nodes in FalkorDB.
- Session-end UX honest during async summarization.

## Revisit conditions

- Publish model changes lumogis_id assignment — revisit projection keying.

## Linear linkage (Product OS)

- **LUM-417**, **LUM-419** — Backlog at retro; close after `dev` merge.

## Testing retrospective

- Vitest `ConversationSidebar` — **14 passed** (with DocumentDetailView) on integration branch.
- `test_graph_delete_session.py` — included in Phase 2 backend gate.

## Status history

- 2026-06-18: Finalised by `/record-retro`.
