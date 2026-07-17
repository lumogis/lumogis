# ADR-165 — Paperless-ngx v0.3 KG mapping: correspondents & tags → graph (LUM-283)

**Status:** Draft (exploration) — recommendation for review, not yet implemented
**Created:** 2026-07-14
**Decided by:** `/explore` (Opus 4.8)
**Linear:** [LUM-283](https://linear.app/lumogis/issue/LUM-283) (project: Knowledge Graph; milestone v1.4) · parent LUM-236
**Builds on:** ADR-054 (paperless v0.1 ingest), ADR-042 (KG public/private export boundary), ADR-158 (graph-aware entity sharing / shared-scope projection), ADR-015 (scopes)

---

## Context

Paperless-ngx ingest ships today (ADR-054): the poller fetches documents incrementally and routes them through `ingest_external_document(external_kind="paperless")` → chunk → embed → Qdrant → LLM entity extraction from body text. LUM-283 asks to deepen this into the **knowledge graph**: map paperless's *structured metadata* — **correspondents** and **tags** (and document types / custom fields) — into FalkorDB as typed nodes and qualifiers, beyond what LLM extraction pulls from body text.

The ticket explicitly requires decisions on three things: (1) the correspondent/tag → graph mapping, (2) sync strategy (webhook vs polling), and (3) **AGPL core vs proprietary packaging** (explicit ADR). This ADR resolves all three.

### What the code shows today

- The poller (`signals/feed_monitor.py:_poll_paperless_source`) already runs incrementally on the shared APScheduler, keyed on `added__gt` with a single-watermark-per-tick guard.
- **The adapter drops the metadata we need.** `adapters/paperless_source.py` parses each `/api/documents/` row into `PaperlessDocument(id, content, added)` only — the response *already contains* `correspondent`, `tags`, `document_type`, `title`, `custom_fields`, but they are discarded. So the fetch side is a cheap, additive extension, not new I/O.
- The graph-projection primitives exist and are proven: ADR-158/LUM-586 wired `project_entity_into_graph` + the refcounted retraction helpers, and established that **shared-scope FalkorDB projection requires `GRAPH_MODE=service`** (LUM-603).
- ADR-042 locks the distribution boundary: the KG implementation (`services/lumogis-graph/`, Falkor adapter, premium overlays) is **stripped from the public AGPL export**; default `GRAPH_MODE=disabled`; Core degrades gracefully when premium modules are absent. The GraphStore Protocol + webhook models are the public contract.

---

## Decision (recommended)

### 1. Mapping schema

- **Correspondent → a `:Correspondent` interim entity node (a Person subtype), not a blind PERSON/ORG guess.** Paperless correspondents are frequently organisations ("Stadtwerke", "HMRC") as often as people, and misclassifying at ingest is hard to unwind. Model them as `:Correspondent` carrying `name` + `source='paperless'` provenance, resolvable/promotable to `PERSON`/`ORG` later by the dedup/entity-resolution pipeline (or by an explicit user action). Each ingested document gets a `SENT_BY` / `CORRESPONDS_WITH` edge to its correspondent node. Correspondent id → name is resolved via a cached `/api/correspondents/` lookup (small, bounded set).
- **Tags → `:Tag` nodes + `TAGGED_WITH` edges, and mirrored into the document entity's `context_tags`.** Tags are the household's own taxonomy ("insurance", "school", "visa") — first-class `:Tag` nodes let "show me everything tagged visa" traverse the graph, while mirroring into `context_tags` keeps them available to the existing recall path. Tag id → name via a cached `/api/tags/` lookup.
- **document_type → an attribute** on the document node (`doc_type='invoice'`); **custom_fields → attributes** (namespaced `cf_*`). No new node types — they are qualifiers, per the ticket's "graph qualifiers/attributes".
- **Provenance:** reuse ADR-158's `share_origin`/`external_kind` pattern — projected correspondent/tag nodes carry `source='paperless'` so retraction on document purge (ADR-158's refcounted `retract_document_entities`) cleans them up correctly.

### 2. Sync strategy: extend the existing poller (polling), not webhooks — for v1

The v0.1 poller already runs on the scheduler and already fetches each document's `correspondent`/`tags` in the same `/api/documents/` response. **The metadata rides the existing poll tick for free** — the only change is to stop discarding it. Paperless *does* support webhooks, but they require operator setup and a reachable callback, which cuts against the zero-config local-first posture and adds a second sync path to maintain. **Recommend polling for v1; note webhook as a later freshness optimisation** (sub-minute graph freshness) if households ask for it. No change to the watermark model.

### 3. Packaging: **premium / proprietary**, per ADR-042 — the projection lives in `services/lumogis-graph/`, gated `GRAPH_MODE=service`

This is the decisive answer to the ticket's packaging question, and ADR-042 already dictates it: **anything that writes to FalkorDB is premium and stripped from the public AGPL export.** Therefore:

- **AGPL public export keeps** the paperless fetch + search ingest (already shipped, no KG) — including the *adapter extension* that captures correspondent/tag metadata (Phase 1 below), since capturing metadata is not KG-writing and is useful to the public search path too (tags in `context_tags`).
- **Premium / proprietary keeps** the correspondent/tag → FalkorDB **projection** (Phase 2): it lives in `services/lumogis-graph/` (stripped), reuses `project_entity_into_graph`, and is gated `GRAPH_MODE=service`. Core degrades gracefully (`GRAPH_MODE=disabled` default) — paperless docs are still ingested + searchable without the graph mapping.

This mirrors ADR-158 exactly (Postgres/Qdrant rows created in any non-disabled mode; FalkorDB projection service-mode-only) and keeps the KG moat intact (ADR-042/101).

---

## Implementation plan (phased by verifiability)

**Phase 1 — metadata capture (AGPL core, verifiable without FalkorDB):**
1. Extend `PaperlessDocument` (additive frozen fields with defaults: `title`, `correspondent_id`, `tag_ids`, `document_type_id`, `custom_fields`) and the adapter parse to retain them.
2. Add cached resolvers over `/api/correspondents/` and `/api/tags/` (id → name), refreshed per poll tick, bounded.
3. Thread the resolved names into `ingest_external_document` as structured metadata; mirror tags into the document entity's `context_tags` (already consumed by search/recall).
   - Unit-testable with mocked HTTP (the scanner-verification bar: standalone + ruff); no FalkorDB needed.

**Phase 2 — graph projection (premium, `GRAPH_MODE=service`, verify on a KG stack):**
4. In `services/lumogis-graph/`, on `DOCUMENT_INGESTED` for `external_kind="paperless"`, project `:Correspondent` + `:Tag` nodes and `SENT_BY`/`TAGGED_WITH` edges via `project_entity_into_graph`, with `source='paperless'` provenance.
5. Extend the ADR-158 refcounted retraction (`retract_document_entities`) to sweep paperless-origin correspondent/tag nodes on document purge/unshare.
6. **Cross-source linking:** a correspondent that also appears via email (LUM-170) or calendar resolves to the *same* entity — route paperless correspondents through the existing entity-resolution/dedup so "everything from HMRC" spans paperless + email. This is the payoff of graph mapping over flat search.

**Verification boundary:** Phase 2 requires `GRAPH_MODE=service` + a running FalkorDB — not available in the exploration environment. Phase 1 is verifiable now; Phase 2 must be verified on a KG-enabled stack (like the LUM-586 test stack, `orchestrator/tests/premium/`).

---

## Alternatives considered

- **Correspondent → blind PERSON/ORG classification at ingest** — rejected: high misclassification rate (orgs vs people), hard to unwind. The `:Correspondent` interim node defers the call to the resolution pipeline.
- **Tags as `context_tags` only (no `:Tag` nodes)** — rejected: loses graph-traversal over the household taxonomy ("everything tagged visa"), which is the whole point of KG mapping vs flat search. Do both.
- **Webhook-first sync** — rejected for v1: operator setup + callback reachability vs the poller already running; revisit for freshness.
- **Ship KG mapping in AGPL core** — rejected: violates the ADR-042 export boundary and the KG moat (ADR-101). Premium/service-mode-only.
- **New `:Document` node per paperless doc** — out of scope: documents are already Qdrant/Postgres first-class; this ADR maps *metadata* (correspondents/tags) into the graph, not the documents themselves.

## Consequences

- **Easier:** reuses the running poller (metadata is free in the existing response), the ADR-158 projection + retraction primitives, and the entity-resolution pipeline for cross-source linking. Phase 1 ships value to the public search path (tags) independent of the premium graph.
- **Harder / watch:** Phase 2 is premium-only and unverifiable off a KG stack; the `:Correspondent` interim type needs the resolution pipeline to eventually promote/merge it; retraction must stay consistent with ADR-158's refcount state machine (the LUM-586 "three teardown sites" caution applies).

## Dependencies

- **ADR-042 / ADR-101** — the packaging boundary this ADR follows (KG = premium, stripped).
- **ADR-158 / LUM-586** — the projection + retraction primitives Phase 2 reuses; `GRAPH_MODE=service` requirement (LUM-603).
- **Entity resolution / dedup** — cross-source correspondent linking (Phase 2.6) rides it.
- **LUM-355 / ADR-163** — paperless connector risk profile (`medium`, read-only) already covers this connector.

## Revisit conditions

- **Correspondent classification** — if the resolution pipeline reliably classifies `:Correspondent` → PERSON/ORG, revisit whether to keep the interim type or classify at ingest.
- **Webhook sync** — if sub-minute graph freshness is requested, add the paperless webhook as a second sync path feeding the same projection.
- **Custom-field typing** — if households use paperless custom fields heavily, revisit whether select fields (dates, money) become typed graph attributes or edges rather than opaque `cf_*` strings.

## Status history

- **2026-07-14:** Draft created by `/explore LUM-283` (Opus 4.8). Resolves the ticket's three explicit asks: mapping schema (`:Correspondent` interim nodes + `:Tag` nodes + qualifiers), sync (extend the poller; webhook later), packaging (premium/service-mode-only per ADR-042). Phased plan splits verifiable metadata capture (AGPL core) from the premium FalkorDB projection (verify on a KG stack). Awaiting review before planning.
