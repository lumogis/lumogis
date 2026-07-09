-- Migration 041: BM25 full-text index on `memories.content` for recall (LUM-295).
--
-- The TEMPR recall fusion (`services/recall.py`) runs a BM25 keyword leg over
-- the `memories` table alongside the semantic (Qdrant), graph (entity_edges),
-- and temporal legs. Postgres native full-text search (`tsvector` + GIN +
-- `ts_rank_cd`) is used rather than a `vchord_bm25`/ParadeDB extension: RRF is
-- rank-based and the cross-encoder rerank fixes final ordering, so the lack of
-- IDF precision in `ts_rank` is largely washed out, and we add no new Postgres
-- extension / Docker-image surface (local-first). See ADR
-- .cursor/adrs/tempr-recall-fusion.md and docs/decisions (finalised at verify).
--
-- `content_tsv` is a STORED generated column: Postgres computes it on write and
-- backfills existing rows during this ALTER (an ACCESS EXCLUSIVE table rewrite —
-- acceptable at the memories table's MVP scale; if it ever grows large, switch
-- to a plain tsvector column + batched UPDATE backfill + trigger). The GIN index
-- serves `content_tsv @@ websearch_to_tsquery('english', :q)` and `ts_rank_cd`.
-- Idempotent (IF NOT EXISTS); additive.
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_memories_content_tsv;
--   ALTER TABLE memories DROP COLUMN IF EXISTS content_tsv;

ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS content_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS idx_memories_content_tsv ON memories USING GIN (content_tsv);
