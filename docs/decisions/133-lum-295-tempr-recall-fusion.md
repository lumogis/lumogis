# ADR-133: TEMPR Recall Fusion for the MCP Memory Server (LUM-295)

**Status:** Finalised
**Created:** 2026-06-25
**Last updated:** 2026-06-25 (finalised by /verify-plan — implementation confirmed the decision)
**Decided by:** /explore → /create-plan → /review-plan (Product OS workflow); finalised by /verify-plan

## Context
The MCP memory server's **write** surface shipped (LUM-291/526): observations land in Postgres (`memories`, system-of-record) and Qdrant (embedding), with bitemporal `valid_until` columns and a soft-archive/supersede path. Retrieval, however, was **Qdrant semantic search alone** — and `services/memories.py` was deliberately write-only pending this work. The reference pattern is **TEMPR** (Hindsight, arXiv 2512.12818): four-way parallel retrieval (semantic, BM25, graph, temporal) → Reciprocal Rank Fusion → cross-encoder rerank. Two facts shaped the option space hard: (1) the **default install has no graph backend** (`GRAPH_BACKEND=none`) and no guaranteed Ollama, and (2) **Qdrant payloads carry no `valid_until`** and archive only mutates Postgres — so validity can only be enforced Postgres-side.

## Decision
A new `services/recall.py` exposes a **read** MCP tool `recall(bank, query, limit, retrieval_strategies, as_of, rerank)` that runs four legs **concurrently** (a per-call `ThreadPoolExecutor`; the MCP surface is fully synchronous) and fuses them with **RRF (k=60)**:

1. **Semantic** — Qdrant search over the `"memories"` collection. The filter uses the explicit `{"must":[{"key":"user_id"…},{"key":"bank"…}]}` shape (a flat dict silently applies no filter in `QdrantStore._build_filter` → cross-user leak); `visible_qdrant_filter` is not reusable (the payload has no `scope` field). Candidate `memory_id`s are read from `payload["memory_id"]`.
2. **BM25** — **native Postgres `tsvector` + GIN + `ts_rank_cd`** over `memories.content` (migration `041-memories-fts.sql`, a `STORED` generated column). *Not* `vchord_bm25` — that extension is deferred as a measured upgrade.
3. **Graph** — 1-hop traversal of **Postgres `entity_edges`** (works on the default `GRAPH_BACKEND=none` install), seeded from query entities via `entities.entity_ids_for_query`, mapped to memories via `evidence_id` (`entity_edges.memories_for_entities`).
4. **Temporal** — the load-bearing contract: every candidate is hydrated/filtered in Postgres with `(valid_until IS NULL OR valid_until >= :as_of)` on `memories` **and** `entity_edges`; default `as_of = now(UTC)`. Hydration runs **before** rerank so the cross-encoder receives candidate `text`.

Post-fusion, an **optional cross-encoder rerank** runs through the existing `ports/reranker.py` Protocol, **reusing the existing `sentence_transformers.CrossEncoder` adapter** (`adapters/bge_reranker.py`, model-agnostic) with **`cross-encoder/ms-marco-MiniLM-L-6-v2`** as the default (`RECALL_RERANKER_BACKEND=cross_encoder|none`, `RECALL_RERANKER_MODEL=…`). **No new dependency** — `sentence-transformers` is already present; it is absent from the Hub PyInstaller freeze, so rerank degrades to `None` there. Each result carries `source_strategies` and `entity_ids` for observability. `recall` is a read tool — it does **not** gate on `mcp:write`.

## Alternatives Considered
- **`vchord_bm25` / ParadeDB `pg_search`** for BM25 — true BM25 scoring but a new Postgres-extension/Docker dependency; the gain is muted because RRF is rank-based and the reranker fixes final order. Deferred.
- **A new ONNX `onnxruntime` reranker adapter** (the original /explore proposal) — superseded at /create-plan: reusing the in-process `sentence_transformers.CrossEncoder` with the MiniLM model achieves the same Ollama-independent, CPU latency profile with zero new dependency; an ONNX adapter remains a drop-in behind the same port if a PoC shows CPU rerank exceeds the p95 budget.
- **Reuse the BGE/Ollama reranker as default** — Ollama-coupled and slower; kept selectable.
- **FalkorDB Cypher as the default graph leg** — null on the default `GRAPH_BACKEND=none` install. Optional accelerator only (LUM-528).
- **Weighted-score fusion** — fails on incompatible BM25/cosine scales; RRF chosen.
- **Filtering validity in Qdrant** — impossible (no `valid_until` in payload); reconcile in Postgres.

Full detail: exploration `.cursor/explorations/LUM-295-tempr-recall-fusion.md`.

## Consequences
**Easier:** Recall surfaces written memories; exact-identifier queries (function names, `LUM-291`, filenames, errors) work via BM25; **LUM-526 archive/supersede is observable** through the temporal filter; future retrieval legs plug in as new RRF ranked lists; the reranker is swappable by env without touching recall.
**Harder / committed:** Postgres is the recall reconciliation substrate (Qdrant candidates always re-checked in Postgres); the MiniLM model artifact must be available/cached for `sentence-transformers` (no new dependency — absent in the Hub freeze where rerank degrades to `None`); the sync legs use a per-call `ThreadPoolExecutor`. `recall` is on the `/mcp/` FastMCP mount (not a REST route), so it does **not** drift the REST OpenAPI snapshot.
**Future chunks must know:** the temporal-filter contract is mandatory on *every* read path; FalkorDB graph acceleration (LUM-528) is optional, not required for recall; LUM-289 is a duplicate of this work.

## Revisit conditions
- If a PoC on a 10k-memory bank shows native `tsvector` recall insufficient on representative exact-identifier queries → revisit `vchord_bm25` (child of LUM-295).
- If p95 > 200 ms with 4 parallel legs + CPU CrossEncoder top-20 rerank → drop in an ONNX MiniLM adapter behind the same port (the `RECALL_RERANKER_BACKEND` seam), and/or revisit leg parallelism.
- If FalkorDB becomes the default backend → revisit the Postgres-only graph leg.
- If MCP tools gain real read-scope enforcement → revisit `recall` gating (today reads never gate).

## Status history
- 2026-06-25: Draft created by /explore (LUM-295).
- 2026-06-25: Revised during /review-plan --arbitrate R1 — reranker decision changed from a new ONNX adapter + onnxruntime dependency to reuse of the existing `sentence_transformers.CrossEncoder` with the MiniLM model (zero new dependency); also corrected async→ThreadPoolExecutor and the OpenAPI-snapshot consequence.
- 2026-06-25: Finalised by /verify-plan — implementation confirmed the decision (all four legs + RRF + temporal hydration + CrossEncoder rerank shipped; cross-model code+security review passed with only P2/P3 hardening). Draft at `.cursor/adrs/tempr-recall-fusion.md`.
