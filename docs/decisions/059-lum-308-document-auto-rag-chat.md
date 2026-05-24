# ADR-059: Document auto-RAG in chat — reranker-gated injection alongside session summaries (LUM-308)

**Status:** Finalised
**Created:** 2026-05-22
**Last updated:** 2026-05-22
**Decided by:** `.cursor/plans/LUM-308-document-auto-rag-chat.plan.md` + `.cursor/explorations/LUM-308-auto-rag-chat.md`
**Finalised by:** `/verify-plan --headless` LUM-308 (2026-05-22)
**Plan:** `.cursor/plans/LUM-308-document-auto-rag-chat.plan.md`
**Exploration:** `.cursor/explorations/LUM-308-auto-rag-chat.md`
**Draft mirror:** `.cursor/adrs/LUM-308-auto-rag-chat.md`
**Linear:** [LUM-308](https://linear.app/lumogis/issue/LUM-308/document-auto-rag-in-chat-inject-relevant-chunks-into-default-context)

## Context

The chat hot path (`orchestrator/routes/chat.py::_inject_context`) previously injected **session summaries** (Qdrant `conversations`) and optional **graph fragments** before message history. Document chunks in the Qdrant `documents` collection were only surfaced when the LLM called the `search_files` tool — which small local models often skip. ADR 039 (injection sanitiser), ADR 051 (context budget allocator), and ADRs 057/058 (correctness caps) constrain how new read-path sources must behave.

## Decision

1. **Opt-in auto-RAG:** When **`LUMOGIS_AUTO_RAG_ENABLED=true`**, `services.auto_rag.retrieve_document_context` queries the `documents` collection with the same **`visible_qdrant_filter`** contract as semantic search, **without** fuzzy filename fallback.

2. **Gating:** With a configured reranker, candidates are reranked with **`rerank_score`** attached (**`BGEReranker.rerank`**), filtered by **`LUMOGIS_AUTO_RAG_MIN_RERANK_SCORE`**, then capped to **`LUMOGIS_AUTO_RAG_TOP_K_POST`** (**gate-then-cap**, never `limit=top_post` before the score filter). Without a reranker (or on reranker failure): **RRF / hybrid** hits use **`threshold=0.40`** + ordering + **`top_k_post`** only — **`LUMOGIS_AUTO_RAG_MIN_BI_ENCODER_SCORE` does not apply** to RRF score space; **dense cosine-comparable** rows use the bi-encoder floor. `qdrant_store.search` exposes **`score_space`** so auto-RAG can distinguish domains.

3. **Injection:** Document plaintext is merged in **session → documents → graph** order, uses the **`documents`** allocator slot (shaved from **`history`**), passes through ADR 039 markup when enabled, and **`Event.CONTEXT_BUILDING`** fires after session + documents are appended (in-process graph may still append after the hook). **`retrieve_document_context`** logs **`event=auto_rag_failed`** and returns `[]` on failure — it does not raise.

4. **Dedupe:** Injected Qdrant **`point_id`** values are tracked in a **mutable set** on **`request.state`**, threaded into **`run_tool` / `dispatch_tool_under_cap`** and **`_search_files`**, so **`search_files`** omits duplicates in the same completion (**streaming-safe** — no `ContextVar` for this set).

5. **HTTP surface:** **`GET /search`** returns **`SearchResult.model_dump(exclude={"rerank_score", "point_id"})`** so internal diagnostics stay off the public JSON contract (**ADR 053 / LUM-94**).

6. **Defaults:** **`LUMOGIS_AUTO_RAG_ENABLED`** defaults **`false`**. Env keys are documented in **`.env.example`** and **`config/test.env.example`**.

## ADR 051 §6 mirror (explicit v1 scope)

ADR 051 requires Core / **`lumogis-graph`** env mirrors for getters consumed in **`GRAPH_MODE=service`**. The KG service **does not** invoke auto-RAG in v1; **`LUMOGIS_AUTO_RAG_*`** is read only from orchestrator **`config.get_auto_rag_*`**. A **future** graph-side reader must add the mirror — tracked as a Linear follow-up under **LUM-308** / **LUM-289** (see plan **Follow-up register**), not as dead config in v1.

## Consequences

- **Easier:** Factual answers over ingested documents without forcing **`search_files`**; **`search_files`** remains available and dedupes against injected **`point_id`**.
- **Harder:** **LUM-295** / **LUM-289** fusion work must consume or replace this hook contract; **LUM-205** / **LUM-140** should treat **`CONTEXT_BUILDING`** as including document fragments when auto-RAG is on.
- **Operator trade-off:** Injected **`document:{file_path}`** attributes may expose paths or titles to the configured LLM — same class as explicit **`search_files`** (documented in **`docs/capabilities.md`**).

## Status history

- **2026-05-22:** Draft created by `/explore --headless LUM-308` (exploration + `.cursor/adrs/LUM-308-auto-rag-chat.md`).
- **2026-05-22:** Revised during `/review-plan --arbitrate` R1 — RRF vs dense gating, **`CONTEXT_BUILDING`** contract, streaming dedupe.
- **2026-05-22:** Finalised by `/verify-plan --headless` — implementation in **`orchestrator/services/auto_rag.py`**, **`routes/chat.py`**, **`services/tools.py`**, **`loop.py`**, **`adapters/bge_reranker.py`**, **`services/search.py`**, **`models/search.py`**, **`models/memory.py`**, **`config.py`** confirmed against plan; tests + **`make openapi-check`** + **`make compose-policy-check`** green in verification environment.
