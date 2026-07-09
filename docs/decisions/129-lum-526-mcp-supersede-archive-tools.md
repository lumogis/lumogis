# ADR-129: MCP supersede/archive tools (forget / update_observation / checkpoint)

**Status:** Finalised
**Created:** 2026-06-24
**Last updated:** 2026-06-24
**Decided by:** `/create-plan` → `/review-plan --self` → `/review-plan --critique sonnet` → `/review-plan --arbitrate` (R1) → `/implement` → `/verify-plan`
**Finalised by:** /verify-plan 2026-06-24
**Linear:** [LUM-526](https://linear.app/lumogis/issue/LUM-526) (epic [LUM-284](https://linear.app/lumogis/issue/LUM-284))
**Plan:** `.cursor/plans/LUM-526-mcp-supersede-write-tools.plan.md`
**Extends:** [ADR 128](128-lum-291-mcp-memory-write-surface.md) (the MCP memory write surface this completes)

## Context

ADR 128 shipped the MVP MCP write surface (`add_memory` / `add_entity` /
`add_relation`) with bitemporal `valid_from`/`valid_until` columns on `memories`
and `entity_edges` — but always wrote `valid_until=NULL` and deferred the
*superseding* behaviour. An agent memory backend (Cursor / Claude Code, epic
LUM-284) needs to retire stale facts: forget a memory, replace an observation
with a corrected one, and mark session boundaries. The open question was the
**destructiveness** of those primitives on an **LLM-driven** surface.

## Decision

Add three more tools on the existing FastMCP `/mcp/` mount, gated by the same
**`mcp:write`** scope: **`forget`**, **`update_observation`**, **`checkpoint`**.

1. **Soft archive only — no hard delete on the MCP surface.** `forget` sets
   `valid_until = now()` on the memory and its evidence edges; it is
   **reversible** (the row and its Qdrant point survive; recall filters by
   validity). True per-memory hard erasure is an irreversible cross-store
   primitive and is kept **off** the LLM-driven surface — deferred to an admin
   command (**LUM-529**). This is the load-bearing decision of this ADR.
2. **Supersede = add-before-archive (fail-safe ordering).**
   `update_observation` stores the new memory **first** (with
   `metadata.supersedes = <old id>`), **then** archives the old one. The two
   writes are not in one transaction (`add_memory` also embeds to Qdrant), so a
   partial failure leaves **both rows active** (recoverable by re-`forget`)
   rather than the old archived with no replacement (memory lost from the active
   view). History is retained — the old row is archived, never deleted.
3. **Refuse re-superseding an archived source.** `get_memory` has no validity
   filter (it returns archived rows so the not-found check still works
   post-archive), so `update_observation` **explicitly refuses** when the source
   is already archived — otherwise it would silently supersede a dead memory.
4. **`checkpoint` is a marker, not prose.** It writes a `metadata.kind=
   "checkpoint"` memory via `store_memory` (threading the required `bank` kwarg)
   and runs **no** entity/relation extraction — a session-boundary signal recall
   may special-case, not text to mine.
5. **Archive `entity_edges`, not `entity_relations`.** `forget` /
   `update_observation` archive the typed inter-entity edges whose `evidence_id`
   is the memory (so its relations drop out of recall with it). The
   `entity_relations` provenance table has **no `valid_until` column** (hard
   schema fact, verified in `init.sql`) and is left untouched — provenance rows
   pointing at an archived memory are harmless (recall never surfaces them) and
   the shared, user-global entities are GC'd separately. No new bitemporal
   migration on `entity_relations`.
6. **One index-only migration (040).** `archive_edges_for_memory` filters
   `entity_edges` by `(evidence_id, user_id)`; migration 039 indexed only
   `src`/`dst`, so `idx_entity_edges_evidence (user_id, evidence_id)` is added to
   avoid a full scan. Additive, no backfill.

## Alternatives considered

- **Hard delete (`delete_memory` / `delete_all_memories`) on MCP** — rejected:
  an irreversible cross-store (Postgres + Qdrant + graph) erasure primitive on a
  tool an LLM can call autonomously is too dangerous. Soft archive is the
  agent-safe equivalent; erasure is admin-only (LUM-529).
- **Archive-before-add in `update_observation`** — rejected: a partial failure
  would archive the old memory with no replacement (lost from active recall).
  Add-before-archive degrades to "both active" instead.
- **A new `valid_until` migration on `entity_relations`** — rejected: out of
  scope; provenance edges pointing at an archived memory are harmless.
- **An `unforget`/restore tool now** — deferred: archived data is already
  recoverable in the DB; a restore tool is an optional follow-up.

## Consequences

- **Positive:** completes the LUM-291 write set; the agent can retire/correct
  facts without any destructive primitive; supersede history is durable
  (`metadata.supersedes` survives a partial failure); reuses the existing scope,
  bitemporal columns, and export registration.
- **Negative / watch:** archive/supersede are only **observable** once recall
  applies the temporal filter (`valid_until IS NULL OR valid_until >= as_of`) on
  `memories` and `entity_edges` — that is **LUM-295**, not this chunk. Until
  then archived rows still surface in recall. `checkpoint`'s `metadata.kind`
  is a new well-known key recall/UX may need to special-case.
- **Idempotency:** `forget` on an already-archived memory is a success no-op
  (`{archived: True}`); `archive_memory` returns False on a repeat (no timestamp
  bump) but the tool still reports archived.

## Revisit conditions

- **LUM-295** implements recall temporal filtering (makes archive observable).
- **LUM-529** ships admin hard-erase (tombstone + sweeper) — reconcile with the
  soft-archive `valid_until` state here.
- A restore/`unforget` tool or a `MEMORY_ARCHIVED` event is introduced.

## Status history

- **2026-06-24:** Finalised by /verify-plan — implementation confirmed the
  decision. 13 LUM-526 tool/helper tests + both cross-cutting guard tests green;
  full suite 2324 passed (7 pre-existing/environmental failures + 1 env-only
  collection error, none touching this surface). Self-review + Sonnet critique
  R1 caught the `checkpoint` missing-`bank` P0, the refuse-already-archived
  guard, and the missing `evidence_id` index (migration 040) — all fixed before
  implementation.
