# ADR-103: LUM-160 document purge follow-ups (LUM-499–505)

**Status:** Finalised  
**Created:** 2026-06-18  
**Last updated:** 2026-06-18  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-06-18 (Composer)  
**Plan:** none — shipped via Claude Code web batch on `integration/claude-2026-06-batch`  
**Exploration:** `.cursor/explorations/lum_160_document_purge_followups_retro.md`  
**Draft mirror:** `.cursor/adrs/lum-160-document-purge-followups.md`  
**Parent ADR:** `docs/decisions/101-lum-160-document-library-ui.md` (amended on integration branch)

## Context

LUM-160 shipped the document library UI and basic multi-store purge. Verify-plan and follow-up issues **LUM-500–505** extended purge reliability, entity GC, document-chat test coverage, mobile nav prove, and Qdrant payload indexing. Work landed on `origin/claude/youthful-maxwell-s3cbjr` without the formal plan/verify loop.

## Decision

Ship as-built on the integration branch:

1. **LUM-500** — `purged_documents` tombstone (`postgres/migrations/032-purged-documents.sql`); `document_purge.py` re-entry retries failed Qdrant/graph arms; web surfaces per-arm errors and retry.
2. **LUM-501** — Migration `033`; inline orphan-entity GC in `purge_document` (Postgres + Qdrant `entities` collection by `entity_id` filter).
3. **LUM-502 / LUM-503 / LUM-499** — Playwright and integration tests for document chat and mobile documents nav; matrix rows `2.3.9` (extended), `2.3.10`.
4. **LUM-505** — Include `file_path` in Qdrant document chunk payload for scoped retrieval alignment.

ADR-101 is amended in-repo to record tombstone, entity GC, and test posture.

## Alternatives considered

- **Background sweeper for document partial purges** — deferred; re-`DELETE` retry sufficient for v1.1; conversation sweeper is LUM-416.
- **Dedicated retry HTTP endpoint** — rejected; re-use `DELETE /api/v1/documents/{id}`.

## Consequences

- Document delete is honest about partial failures; operators have UI retry without psql.
- Graph entity nodes on last-document removal remain deferred (same as LUM-501 exploration note).
- Migrations `032` and `033` must apply before deploy.

## Revisit conditions

- Sustained partial document purge rate triggers LUM-500 acceptance threshold → background reconciliation issue.
- Multi-user graph entity ownership rules → extend GC to FalkorDB `Entity` nodes.

## Linear linkage (Product OS)

- **LUM-500**, **LUM-501**, **LUM-502**, **LUM-503**, **LUM-499**, **LUM-505** — evidence on `integration/claude-2026-06-batch`; Linear may show Done before `dev` merge — post SHA evidence via `/linear-update comment`.

## Testing retrospective

- `pytest orchestrator/tests/test_document_purge.py` — **22 passed** (with embedding readiness) on integration branch.
- Vitest `DocumentDetailView` — **7 passed**.
- Integration `test_document_chat_scoped.py` — skipped without stack.

## Status history

- 2026-06-18: Finalised by `/record-retro` (retrospective).
