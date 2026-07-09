# ADR-106: Unscoped api/v1 chat auto-RAG injection (LUM-504)

**Status:** Finalised  
**Created:** 2026-06-18  
**Last updated:** 2026-06-18  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-06-18 (Composer)  
**Plan:** none — Claude Code web batch  
**Exploration:** `.cursor/explorations/lum_504_unscoped_auto_rag_retro.md`  
**Draft mirror:** `.cursor/adrs/lum-504-unscoped-auto-rag.md`  
**Builds on:** ADR-059 (LUM-308 auto-RAG), ADR-101 (LUM-175 document chat)

## Context

`POST /api/v1/chat/completions` with `document_id` activates scoped injection (LUM-175). Unscoped requests previously skipped `build_injected_context`, leaving session memory and auto-RAG inactive on the v1 API surface.

## Decision

When `document_id` is absent, `_resolve_scoped_injection()` calls `build_injected_context()` without `scoped_file_path`, enabling session memory retrieval, auto-RAG (feature-flag gated), and graph snippets. Failures fall back to plain history. `auto_rag_point_ids` pass through to `ask`/`ask_stream` for deduplication.

## Alternatives considered

- Keep v1 unscoped chat injection-free — rejected (parity with legacy chat route).

## Consequences

- Unscoped v1 chat matches LUM-308 injection posture; feature flags still gate auto-RAG globally.

## Revisit conditions

- Context budget changes for v1 vs legacy routes must stay aligned in `routes/chat.py`.

## Linear linkage (Product OS)

- **LUM-504** — Done in Linear before `dev` merge; post evidence comment with integration SHA.

## Testing retrospective

- `orchestrator/tests/test_api_v1_chat.py` — unscoped injection + streaming cases; Phase 2 gate green.

## Status history

- 2026-06-18: Finalised by `/record-retro`.
