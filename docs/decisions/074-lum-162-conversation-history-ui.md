# ADR-074: Conversation history UI — persistence, API, and multi-store purge

> Status: Active (numbering conflict)
> Last reviewed: 2026-07-10
> Verified against commit: 01a7d31
> Notes: **`docs/decisions/074-lum-178-stack-health-dashboard.md`** also claims **ADR 074** in its title. **[ADR 085](085-lum-439-conversation-put-upsert-fix.md)** amends this record as the canonical **ADR 074** conversation-history programme; renumber **`074-lum-178-*.md`** to **`159-lum-178-stack-health-dashboard.md`** in a coordinated pass (**110** is ingest job progress **LUM-511**; **111**–**158** retros now allocated — note three **154** and three **156** prefixes; **155** is LUM-157 content projection; **157** is post-ship sharing fixes; **158** is LUM-586 graph-aware entity sharing). Filename prefixes **049–158** are already in use under `docs/decisions/` (duplicate clusters on **053**, **059**, **060**, **061**, **063**, **064**, **072**, **074**, **098**, **101**, **147**, **154**, **156**, plus **`065-lum-320-*.md`** through **`158-graph-aware-entity-sharing-lum586.md`**). Pick a **non-colliding** new slug when renumbering—coordinate with any **`034-linear-evidence-index.md`** / **046** / **072** rename in the same pass—see `docs/_librarian/docs-inventory.md`.

**Status:** Finalised
**Created:** 2026-06-01
**Last updated:** 2026-06-15
**Decided by:** /explore --headless LUM-162; finalised by /verify-plan 2026-06-01

## Context

LUM-162 (under the LUM-44 Lumogis Web programme, `milestone:v1.1`) asks for a UI to browse, continue, and delete past conversations. The repo had an intentional v1 split: web chat threads were ephemeral per-tab `sessionStorage`, while the orchestrator persisted only a session summary (Postgres `sessions` + Qdrant `conversations` + optional FalkorDB `Session` node). There was no conversation list/detail/delete API and no multi-store purge helper (a gap shared with LUM-91 captures).

## Decision

Ship a **phased hybrid** in a single implementation chunk (slice 1 + slice 2):

- **`/api/v1/conversations`** — list, detail, delete, continue, plus slice-2 mutators (`PUT`, `POST` messages, `POST` create) via `services/conversations.py` and `routes/api_v1/conversations.py`.
- **`conversation_id` === `session_id` (UUID v4)** — enforced on web thread mint, `POST /session/end`, and API path params.
- **`memory_purge.purge_session_memory`** — transactional Postgres delete (sessions, projections, `web_*` rows), bounded Qdrant/graph retry (3×100 ms), honest `partial=true` on vector/graph exhaustion.
- **`delete_session`** graph writer — `DETACH DELETE` personal `Session` node only.
- **Lumogis Web** — `ConversationSidebar` on `/chat`, `POST /session/end` wiring, debounced transcript sync to `web_conversations` / `web_messages` (migration **027**).
- **UX** — sidebar grouping (Today / Yesterday / Last 7 days / Older); continue mints a new client thread with `LOAD_SEED_MESSAGES`; hard delete with partial-failure toast.

## Alternatives considered

See `.cursor/explorations/LUM-162-conversation-history-ui.md` and draft ADR history in `.cursor/adrs/LUM-162-conversation-history-ui.md`.

## Consequences

- **Easier:** trust surface and true delete ship together; LUM-91 can reuse purge primitives; transcript persistence is additive behind one API contract.
- **Harder:** server-side chat transcripts are now stored in Postgres — documented in `SECURITY.md` and the reference manual; operators must run migration **027**.
- **Follow-up:** reconciliation sweeper for orphan Qdrant/graph copies after `partial=true` (**LUM-416**); optional graph purge for published projection Session nodes (**LUM-419**); Playwright coverage; **`GET /api/v1/memory/recent`** empty-list fix shipped (**LUM-418** — `SessionSummary.updated_at` projected from `sessions.updated_at`).

## Status history

- 2026-06-01: Draft created by /explore --headless LUM-162
- 2026-06-01: Revised during /review-plan --arbitrate R1 (bounded retry + honest partial UX)
- 2026-06-01: Finalised by /verify-plan — implementation confirmed (LUM-162)
- 2026-06-15: Amended consequences follow-up — **LUM-418** shipped `/api/v1/memory/recent` `ended_at` mapping via `SessionSummary.updated_at` (no ADR decision change)
