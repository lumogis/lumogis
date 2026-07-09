# ADR-101: Document chat mode — scoped conversation pinned to one document (LUM-175)

**Status:** Finalised
**Created:** 2026-06-15
**Last updated:** 2026-06-19
**Decided by:** `/explore --headless LUM-175`; implementation verified `/verify-plan --headless` 2026-06-15
**Exploration:** `.cursor/explorations/LUM-175-document-chat-mode.md`
**Linear:** [LUM-175](https://linear.app/lumogis/issue/LUM-175)

## Context

LUM-308 (ADR-059) shipped `services.auto_rag.retrieve_document_context`, which injects reranker-gated chunks from the Qdrant `documents` collection into the chat hot path under the `visible_qdrant_filter` household-scope contract. LUM-175 adds "chat with this document" mode: a conversation pinned to a single library document, grounded only in that document's chunks, usable by small local models without relying on tool-calling.

Document identity uses Bridge A: `file_index.id` (INTEGER PK from LUM-160) resolves to `file_path` via Postgres under `visible_filter`; Qdrant chunks are filtered by AND-merged `file_path` match.

## Decision

Implement document chat by extending the existing auto-RAG primitive and chat pipeline:

1. Optional `document_id: int | None` on `POST /api/v1/chat/completions`; when set, resolve via `document_scope.resolve_document_file_path` and call `build_injected_context` (promoted from `_inject_context`) at the **api_v1 route** before `loop.ask`/`ask_stream`. Injection is route-level only — not threaded through `SessionParams`.
2. Scoped retrieval bypasses global `LUMOGIS_AUTO_RAG_ENABLED`, AND-merges `file_path` into `visible_qdrant_filter`, uses elevated scoped `top_k` (defaults 40/12 via `LUMOGIS_DOCUMENT_CHAT_TOP_K_*`), relaxed relevance floors (0.30/0.45), and propagates infra failures as **503** `auto_rag_failed`.
3. **Source-only scoped mode:** suppress session-memory and graph fragments; widen `documents` budget slot; force `use_tools=False`; pre-flight **422** `document_context_unavailable` when zero post-fit citations.
4. Return `lumogis.context_citations` on streaming (first SSE chunk) and non-stream responses.
5. Web route `/documents/:documentId/chat` with **Context used** citation strip (chunk indices).

**Descoped from v1:** legacy `/v1/chat/completions` document threading; dedicated `POST /api/v1/documents/{id}/chat` endpoint. **Unscoped api/v1 auto-RAG** shipped as **[ADR 106](106-lum-504-unscoped-auto-rag.md)** (**LUM-504**, 2026-06-18).

## Alternatives Considered

- Dedicated documents chat endpoint — drift risk vs canonical `api_v1/chat.py`; deferred.
- Tool-based scoping — regresses ADR-059 for small models; rejected.
- Bridge B (`file_index_id` on payload + reindex) — deferred for multi-document follow-up.
- Per-document collection / new port — rejected in exploration.

## Consequences

- **Easier:** Reuses LUM-308 retrieval; no migration or re-ingest; authorisation via existing visibility contract.
- **Harder:** Hot-path behavioural branch when `document_id` set; Bridge A commits chat identity to id→path mapping until Bridge B.
- **Operator:** Same path exposure class as ADR-059 auto-RAG; scoped mode ignores global auto-RAG off switch.

## Revisit conditions

- Multi-document or page-range pinning → Bridge B + Qdrant `groups`.
- Scoped-filter latency at scale → Qdrant payload index on `file_path`.
- `external_documents` scope → LUM-281.
- Bespoke per-turn document-chat features → dedicated endpoint.

## Status history

- 2026-06-15: Draft created by `/explore --headless LUM-175`.
- 2026-06-15: Revised during `/review-plan --arbitrate` R2 — scoped injection only; elevated top_k; infra→503; no hook kwarg change.
- 2026-06-15: Finalised by `/verify-plan --headless` — implementation confirmed.
- 2026-06-19: Descoped-item note updated — unscoped api/v1 auto-RAG shipped as **ADR 106** (**LUM-504**).
