# ADR-066: Memory as hint not ground truth — entity `memory_type`, derived confidence, system-prompt hedging

**Status:** Finalised
**Created:** 2026-05-27
**Last updated:** 2026-05-27
**Decided by:** `/explore --headless LUM-124` (Claude Opus 4.7); **revised 2026-05-27** during `/review-plan --arbitrate` R1 (GPT-5.2); **finalised 2026-05-27** by `/verify-plan --headless` — implementation matches Decision (see Lumogis product commit on **`agent/lum-124`**).

## Context

Lumogis currently injects entity-retrieval fragments and session/document excerpts into the chat context with an `ack_msg` of "Acknowledged excerpts are reference-only scaffolding." There is no mechanism telling the LLM how *uncertain* a retrieved entity is, whether the underlying mentions are stale, or what kind of memory the entity represents (a user's explicit preference, a correction, or a derived relationship). Retrieved entities are extracted by spaCy NER over ingested documents and are inherently derived — they may be contradicted by newer documents and should not be treated as ground truth.

The 2026 research literature (True Memory, BeliefMem, Hindsight/TEMPR, MINTEval, Self-Aware Vector Embeddings) consistently shows that calibrated `confidence`, explicit `last_verified_at` decay, and a closed `memory_type` taxonomy are the three minimum ingredients for a memory layer that does not silently hallucinate as the knowledge base ages. Claude Code's source-leak material confirms the closed-taxonomy approach works in production. External memory layers (Zep, Mem0, Letta, Cognee) all encode the same ingredients, but with an operational cost (extra services, Neo4j, cloud APIs) that is incompatible with Lumogis's local-first, no-new-Docker-services constraints and with FP-031 (LUM-40) which is actively removing graph machinery from Core.

## Decision

Adopt a Lumogis-specific 3-type entity `memory_type` taxonomy — `user_preference`, `correction`, `relationship` — and surface `memory_type`, a derived scalar `confidence ∈ [0, 1]`, and `last_verified_at` on the entity-retrieval response. **v1 retrieval-time signals (Postgres `entities` as implemented today):** `mention_count`, `updated_at` (staleness / age proxy), and (post-migration) persisted `memory_type` / `last_verified_at`. Confidence combines a **log mention-saturation curve** `g(mention_count)` with **exponential decay** `2^(-age_days / halfLife_days)` using per-entity-type half-lives (Person P90D, Project P14D, Organisation P180D, Concept P365D, **File P30D** — initial estimates); when age is unknown, apply an explicit **conservative factor (0.6)** on the mention-only term before clamping to `[0,1]` to avoid over-crediting undated high-mention rows. A global "memory-as-hint" instruction is added to chat `_inject_context` ack scaffolding, gated by `LUMOGIS_MEMORY_HINT_ENABLED` (default on). In v1, only `memory_type='correction'` is persisted to FalkorDB entity nodes; the other two types are derived. The work ships in two slices (prompt-only quick win first; schema fields second) so that LUM-205 (Web chat, `blocked_by: [LUM-124]`) can de-risk in parallel.

## Alternatives Considered

- **B — 1:1 Claude Code 4-type taxonomy** (`user` / `feedback` / `project` / `reference`): rejected because `memory_type='project'` collides with Lumogis's existing `Project` entity type and `reference` collides with the connector-credentials concept; see `.cursor/explorations/LUM-124-memory-as-hint.md` § Option B.
- **C — External memory layer (Zep / Mem0 / Letta / Cognee)**: rejected; doubles the persistence layer while FP-031 (LUM-40) is removing one, requires new Docker services (or cloud APIs), and provides no capability that cannot be reproduced with three derived properties on a FalkorDB node.
- **D — Belief bank with multiple competing hypotheses per entity (BeliefMem-style)**: deferred; premature for current corpus volatility; `confidence` chosen in this ADR is forward-compatible as the first marginal of a future probability distribution.
- **E — Prompt-only hint instruction, no schema change**: insufficient alone — does not unblock LUM-122 (`memory_type` for compaction), LUM-114 (`correction` for feedback), or LUM-369 (shared confidence framework). Adopted as **slice 1** of the chosen Option A.
- **F — Merge with LUM-369 (edge confidence) into one exploration**: rejected because it would further block LUM-205. Instead, the **decay formula and per-type half-lives** chosen here are frozen project-wide so LUM-369 can adopt them without re-litigation.

Full comparison and references in `.cursor/explorations/LUM-124-memory-as-hint.md`.

## Consequences

**Becomes easier:**

- The LLM consistently hedges low-confidence or stale entities at the *clause* level (per arXiv 2604.17487 / 2604.05306), reducing the OWASP ASI06 "memory poisoning treated as ground truth" failure mode for household corpora.
- LUM-122 (compaction) gets a stable `memory_type` to drive preserve-vs-compress without re-deriving categories.
- LUM-114 (feedback loop) has a defined label (`correction`) to write against, even before its own schema lands.
- LUM-369 (edge confidence) inherits the same decay formula and parameters, keeping node and edge confidence composable in retrieval ranking.
- LUM-205 (Web chat entity chips) gets `memory_type` and `confidence` for free if the UX call (Q3 in exploration open questions) decides to surface them.

**Becomes harder / costs incurred:**

- Operators tuning chat behaviour now have **several** new env vars to reason about (`LUMOGIS_MEMORY_HINT_ENABLED`, per-type half-life overrides including **FILE**, threshold, stale-days diagnostics, correction placeholder flag). Defaults must be conservative.
- The `correction` write path (slice 2) couples loosely to LUM-114; a placeholder writer ships here, real ingestion lands with LUM-114.
- Per-entity-type half-life defaults are educated guesses; first month on `dev` will likely retune them. Telemetry to observe this is **not** in this ADR and should be a separate LUM ticket.
- `LUMOGIS_SCHEMA.md` (LUM-366) must document `memory_type`, `confidence`, `last_verified_at` once it lands.

**Forecloses:**

- Introducing `memory_type='project'` or `memory_type='reference'` later (the 3-type taxonomy is closed for v1).
- Bolting on an external memory service (Zep/Mem0/Letta/Cognee) without an ADR revisit.

**Does not foreclose:**

- A future BeliefMem-style probability bank — `confidence` becomes the first marginal.
- Per-edge confidence in LUM-369 — orthogonal to per-node confidence here.
- Per-source `source_reliability_index` (separate exploration, out of scope).

## Revisit conditions

Revisit this decision when **any** of the following becomes true:

- LUM-369 lands and demonstrates that the chosen exponential-decay formula or per-type half-lives are systematically miscalibrated against observed edge-stability telemetry.
- LUM-114 ships a feedback schema that conflicts with the placeholder `correction` write path (rename, structural change, multi-field correction record).
- Production chat logs show that prompt-only hedging (slice 1) is sufficient to remove perceived hallucination — in which case slice 2 may be downsized.
- Production chat logs show that derived confidence systematically over-credits high-mention but unverified entities — in which case persistence of `confidence` (not just derivation) becomes warranted.
- A future evaluation of belief-bank approaches (Option D) demonstrates a meaningful retrieval-quality win on Lumogis household corpora.
- The Lumogis household corpus exceeds the scale where retrieval-time derivation of `confidence` becomes a measurable latency hit (target: keep the per-fragment overhead under 5 ms; revisit if exceeded).

## Status history

- 2026-05-27: Draft created by `/explore --headless LUM-124`.
- 2026-05-27: Revised during `/review-plan --arbitrate` R1 — **Decision** confidence inputs updated: removed references to non-existent `entities.co_occurrence_count` / `entities.last_mention_at`; documented v1 `g(mention_count)` × exponential decay, **FILE** half-life bucket, and **0.6** unknown-age dampening; injection-sanitiser path uses **prepended** hedge before nonce ack per plan.
- 2026-05-27: Finalised by `/verify-plan --headless` — code review: `orchestrator/routes/chat.py`, `services/lumogis-graph/graph/query.py`, migration **`026-entities-memory-type.sql`**, `memory_types.py`, writer placeholder path; tests green under **`cd orchestrator && .venv/bin/python -m pytest`** + graph package tests.

## Implementation notes (as-shipped)

- Canonical string literals live in **`services/lumogis-graph/graph/memory_types.py`**.
- **`[Graph]`** lines append the frozen suffix **` (hint: type=…; confidence=…; last_seen=…)`** from **`on_context_building`** (`query.py`).
- **`Event.CONTEXT_BUILDING`** comment in **`orchestrator/events.py`** documents that structured hook kwargs for these fields are **out of scope** for v1.
- **`LUMOGIS_MEMORY_CORRECTION_PLACEHOLDER`** gates **`mark_entity_correction_on_graph`** from **`on_feedback_received`** in **`graph/writer.py`** (default off).
