# Lumogis Knowledge Graph Quality Strategy

**Sustaining a high-quality, self-healing personal knowledge graph**

> Status: Strategic design document — post M1-M3 implementation
> Scope: Tool development (primary) and operational curation (secondary)
> Purpose: Translate research findings into Lumogis requirements and implementation prompts

---

## Contents

1. Executive Summary
2. Current State Assessment — What M1-M3 Gives Us
3. The Four Quality Problems and Their Root Causes
4. Strategic Architecture — The Quality Pipeline
5. Tool Development Requirements
   - 5.1 Entity Extraction Post-Processor
   - 5.2 Probabilistic Entity Resolution (Splink Integration)
   - 5.3 Edge Quality Scoring
   - 5.4 Temporal Decay Engine
   - 5.5 Constraint Validation Layer
   - 5.6 Graph Health Dashboard
   - 5.7 Human Review Queue
   - 5.8 Drift Detection Service
6. Operational Curation Workflow
7. Implementation Sequencing
8. Open Questions and Known Limits

---

## 1. Executive Summary

M1 through M3 established the structural foundation: FalkorDB is wired, entities project into the graph, the graph enriches chat, and the reconciliation loop keeps it self-healing. What M1-M3 does not yet address is the quality of what goes into the graph. A self-healing graph that reliably heals back to a noisy, duplicate-laden state is not the goal.

This document defines the quality strategy for Lumogis: what to build, in what order, and how to operate it. The research basis is the comprehensive literature review covering entity resolution, noise filtering, edge quality, KG quality frameworks, and concept drift.

> **Core finding:** The primary quality risk in Lumogis is not the graph layer — it is the entity extraction pipeline upstream of it. FalkorDB faithfully projects whatever Postgres contains. If Postgres contains duplicate entities and noisy extractions, the graph is noisy and fragmented. Fixing quality at the source is more valuable than fixing it in the graph after the fact.

### Three strategic priorities

1. **Fix entity quality at extraction time** — post-processing filters and composite quality scoring applied immediately after spaCy NER output, before anything reaches Postgres.
2. **Add probabilistic deduplication** — Splink-based entity resolution running against the existing Postgres entity table, using Qdrant for embedding-based blocking. Addresses the duplicate entity problem without labeled training data.
3. **Score and decay edges** — replace raw co-occurrence counts with PPMI-filtered, temporally-decayed edge weights. Addresses spurious edges and makes the graph signal-to-noise ratio improve over time rather than degrade.

### What this is not

This document does not describe a big-bang rewrite. Every component described here is additive — it slots into the existing pipeline at a well-defined seam. The tri-store model (Postgres/Qdrant/FalkorDB) is unchanged. The hook architecture is unchanged. The reconciliation loop is unchanged and becomes more valuable as quality improves upstream.

---

## 2. Current State Assessment — What M1-M3 Gives Us

### What is working

- FalkorDB adapter and graph plugin are operational, with MERGE-idempotent writes and `graph_projected_at` reconciliation.
- The 3-tier entity resolution in `services/entities.py` (merge/ambiguous/create) provides the right decision structure — the issue is the quality of inputs to that decision, not the structure itself.
- Qdrant stores entity embeddings, enabling vector-based blocking for deduplication without a separate embedding step.
- The hook architecture (`ENTITY_CREATED`, `DOCUMENT_INGESTED`, `SESSION_ENDED`) provides clean extension points for quality signals.
- Postgres is the source of truth with UUID-keyed entities, `aliases` array, `mention_count`, and `context_tags` — all of which can be leveraged by a quality pipeline without schema changes.

### What is not yet addressed

| Problem | Current state | Impact on graph |
|---|---|---|
| Noisy entity extraction | No post-processing filters on spaCy NER output | Generic terms ("the meeting", "this project") enter the graph as nodes |
| Duplicate entities | String-exact alias matching only; no fuzzy or embedding-based resolution | "Sarah Chen", "Sarah", "S. Chen" become three disconnected nodes |
| Spurious co-occurrence edges | Raw co-occurrence count is the only edge quality signal | Entities sharing a long document get edges regardless of semantic relationship |
| No edge temporal decay | `co_occurrence_count` only increments, never decays | Old relationships have equal weight to current ones |
| No constraint validation | No type-level or schema-level validation on ingestion | Missing properties, invalid edge types, self-loops go undetected |
| No graph health visibility | No dashboard metrics beyond node/edge counts | Quality degradation is invisible until queries return obviously wrong results |
| No drift detection | Entity profiles have no temporal consistency checking | Renamed projects, changed roles, evolving aliases accumulate silently |

---

## 3. The Four Quality Problems and Their Root Causes

### 3.1 Duplicate entities

**Root cause:** The current 3-tier resolution relies on exact and near-exact string matching against the entity name and `aliases` array. It has no mechanism for resolving cross-document surface form variation — nicknames, initials, abbreviations, or simple inconsistency in how a person or organisation is named across different source documents.

**Consequence in the graph:** Instead of a single well-connected Person node with many MENTIONS edges, the same person exists as 3-5 weakly-connected nodes. Ego network queries return fragments. The CONTEXT_BUILDING injection fires on only one variant. The user's question "What do you know about Sarah?" returns partial results.

> **Research basis:** Fellegi-Sunter probabilistic record linkage (1969) provides the theoretical foundation. Splink implements this with unsupervised EM parameter estimation — no labeled training data required. For personal names specifically, a multi-signal approach is required: Jaro-Winkler on name components, nickname database lookup, initial expansion, and embedding cosine similarity as a secondary signal.

### 3.2 Noisy entities

**Root cause:** spaCy NER extracts any noun phrase that matches a named entity pattern. Informal text (notes, transcripts, chat) is particularly noisy — generic phrases like "the meeting", "this project", "the client", and "the team" are frequently elevated to entity status. There is currently no post-processing filter.

**Consequence in the graph:** The graph accumulates hundreds of low-value Concept and Organisation nodes that pollute ego networks, degrade co-occurrence edge quality (generic entities co-occur with everything), and create noise in the CONTEXT_BUILDING injection. The `mention_count` and `co_occurrence_count` thresholds provide a partial gate but do not discriminate between a genuine low-frequency entity and a generic phrase.

> **Research basis:** A composite quality score combining NER confidence (when available from spaCy spancat), POS composition (entities with all-common-noun tokens are suspect), mid-sentence capitalisation, determiner presence, and a stop-entity list is the practical approach. The stop-entity list is the single highest-ROI filter — a curated list of 50-100 known generic phrases eliminates the majority of noise at near-zero cost.

### 3.3 Spurious and decaying edges

**Root cause:** Co-occurrence is the only signal for RELATES_TO edges. Two entities appearing in the same document creates an edge regardless of whether they have a meaningful relationship. The edge weight only increments — it never decays — so a relationship that was relevant two years ago has the same weight as one that is actively current.

**Consequence in the graph:** Path queries return connections that are statistically coincidental. Ego networks are over-dense with low-signal edges. The CONTEXT_BUILDING injection may surface stale or irrelevant relationships. The graph's signal-to-noise ratio degrades over time as more documents are ingested.

> **Research basis:** Pointwise Mutual Information (PPMI) filters edges where co-occurrence is not statistically significant above chance. Sentence-level co-occurrence is far more reliable than document-level. Exponential temporal decay with per-edge-type configurable half-lives addresses staleness. Different edge types warrant different half-lives: DISCUSSED_IN ~30 days, MENTIONS ~180 days, RELATES_TO ~365 days, DERIVED_FROM no decay.

### 3.4 Concept drift

**Root cause:** Entity profiles have no temporal consistency model. When a new document refers to "VP Marketing" for someone previously known as "Head of Marketing", either a new entity is created (if string matching fails) or the existing entity is silently updated (if matching succeeds). Either way, the change is not tracked and the historical state is lost.

**Consequence in the graph:** The graph cannot answer temporal questions. Drift events accumulate silently. Users cannot understand why an entity's connections changed. Manual deduplication decisions are hard to make without understanding the entity's history.

> **Research basis:** Bi-temporal modelling (valid_from/valid_to on attributes, invalidated-but-not-deleted edges) is the established solution, implemented in production by Graphiti/Zep. For Lumogis, a pragmatic lightweight version — an alias change log and attribute version history stored in Postgres — provides drift detection without full temporal graph complexity.

---

## 4. Strategic Architecture — The Quality Pipeline

The quality pipeline is not a separate system — it is a set of quality gates and enrichment steps inserted at existing seams in the Lumogis pipeline. The architecture has three phases.

### Phase A — Extraction quality (upstream gate)

Applied immediately after spaCy NER, before entities reach Postgres. This is the highest-leverage phase: entities rejected here never pollute the graph.

- Post-processing filters: stop-entity list, POS composition check, determiner check, length check, capitalisation check
- Composite quality score: combines NER confidence, frequency signal, and structural signals into a 0-1 score
- Routing: score above upper threshold → ingest normally; score between thresholds → ingest to staging tier; score below lower threshold → discard

### Phase B — Entity resolution (deduplication)

Applied after entity creation, against the existing entity corpus. This is the highest-complexity phase, but Splink's unsupervised approach makes it feasible without labeled data.

- Blocking: type-based (Person vs Person only) + Qdrant ANN search (top-10 embedding neighbours) + first-2-character attribute blocking
- Scoring: Splink Fellegi-Sunter model using Jaro-Winkler on name components, nickname database, initial expansion, embedding cosine similarity
- Decision: auto-merge above upper threshold; queue for human review between thresholds; create as distinct below lower threshold
- Feedback loop: human decisions update alias tables and refine Splink thresholds

### Phase C — Edge quality and temporal maintenance

Applied to RELATES_TO edges, both on creation and via a scheduled maintenance job. This is the most ongoing phase — it runs continuously as new data is ingested.

- PPMI scoring: filter edges where co-occurrence is not statistically significant above chance
- Sentence-level gating: weight sentence-level co-occurrence higher than paragraph-level
- Temporal decay: exponential half-life applied per edge type, updated by scheduled job
- Constraint validation: per-type rules run on each ingestion batch

> **Key architectural principle:** All quality components write their results back to Postgres, not directly to FalkorDB. The existing reconciliation loop (M2) then projects quality-enriched Postgres state into FalkorDB on the next cycle. This preserves the tri-store architecture and ensures FalkorDB remains a projection of Postgres, not an independent source of truth.

---

## 5. Tool Development Requirements

The following eight components constitute the complete quality toolchain. Each is described with its purpose, inputs/outputs, implementation approach, key decisions, and test requirements. They are ordered by implementation priority.

---

### 5.1 Entity Extraction Post-Processor

**Purpose:** Filter noisy entities from spaCy NER output before they reach the 3-tier resolution pipeline in `services/entities.py`. This is the highest-ROI quality component — cheap to implement, immediate impact.

**Integration point:** Insert as a filter step in `services/entities.py`, immediately after the NER model produces entity spans and before the merge/ambiguous/create decision. The post-processor receives a list of candidate entity spans and returns a filtered, scored list.

#### Filter stages

| Stage | Description |
|---|---|
| Stop-entity list | Exact and case-insensitive match against a maintained list of known generic phrases. Start with ~100 entries covering common noise patterns: "the meeting", "the project", "the client", "the team", "the call", "the document", "this week", "next steps". Store in a config file, not hardcoded. Allow user additions via the dashboard. |
| Length and composition | Reject entities shorter than 2 characters. Reject purely numeric tokens unless `entity_type` is DATE or MONEY. Reject entities where all tokens are lowercase common words. |
| POS composition | Entities whose token composition is all common nouns (NN tag, not NNP) are suspect. Compute a proper-noun ratio: NNP or NNPS tokens / total tokens. Score below 0.5 on a multi-token entity is a noise signal. |
| Determiner presence | Entities preceded by determiners (the, a, an, this, that, these, those) are more likely generic noun phrases. Flag but do not automatically reject — some proper nouns are legitimately preceded by "the". |
| Mid-sentence capitalisation | In English, mid-sentence capitalisation strongly signals a proper noun. Score entities with unexpected mid-sentence capitalisation higher. |
| Composite quality score | Combine signals into a 0.0-1.0 score. Suggested weights: NER confidence (0.3), proper-noun ratio (0.25), stop-entity absence (0.2), capitalisation signal (0.15), determiner absence (0.1). Entities below 0.35 are discarded. Entities 0.35-0.60 enter staging. Entities above 0.60 proceed normally. |

#### New Postgres columns

Add to the `entities` table:
- `extraction_quality FLOAT` — composite quality score, default NULL for existing rows
- `is_staged BOOLEAN` — default FALSE; staged entities excluded from graph projection and CONTEXT_BUILDING injection

#### Staging tier

Entities scoring 0.35-0.60 are created with `is_staged=TRUE`. Staged entities are excluded from graph projection and CONTEXT_BUILDING injection until promoted. Promotion triggers:
- Re-mention in a new document and score rises above upper threshold (auto-promote)
- Explicit user confirmation via the review queue
- `mention_count` exceeding a configurable threshold (`ENTITY_PROMOTE_ON_MENTION_COUNT`, default 3)

#### Key decisions for implementation prompt

- Stop-entity list location: `orchestrator/config/stop_entities.txt`, loaded at startup, reloaded on SIGHUP
- Quality score storage: on the `entities` row, not in a separate table
- Staging implementation: `is_staged BOOLEAN` on entities table, filtered in `reconcile.py` and `context.py` queries
- Thresholds: configurable via env vars `ENTITY_QUALITY_LOWER=0.35`, `ENTITY_QUALITY_UPPER=0.60`
- Tests: verify stop-entity list filtering, POS composition scoring, staging routing, threshold configuration

---

### 5.2 Probabilistic Entity Resolution (Splink Integration)

**Purpose:** Detect and resolve duplicate entities in Postgres using probabilistic record linkage. Addresses the core duplicate entity problem without requiring labeled training data.

**Library:** Splink (UK Ministry of Justice, Python, PostgreSQL backend supported). Unsupervised EM parameter estimation — no labeled pairs required. The Splink model is trained on the existing entities corpus and saved/reloaded for incremental use. As human decisions accumulate, the model can optionally be retrained with partial labels to improve accuracy.

#### Blocking strategy

| Blocker | Implementation |
|---|---|
| Type-based | Only compare entities of the same `entity_type`. Reduces candidate space by ~80% for a balanced corpus. |
| Qdrant ANN | For each entity, query Qdrant `entities` collection for top-10 nearest neighbours by embedding. Entity pairs within the neighbour set are blocking candidates. Leverages existing embeddings at zero extra cost. |
| Attribute blocking | Entities sharing the first 2 characters of normalised name are candidates. Standard Fellegi-Sunter blocking key. |
| Nickname expansion | Before blocking, expand known nicknames using the Python `nicknames` library (or a custom hypocorism table). "Bob" and "Robert" resolve to the same canonical form for blocking purposes. |

#### Scoring model

The Splink model compares candidate pairs on:
- Jaro-Winkler similarity on normalised first name component
- Jaro-Winkler similarity on normalised last name component (if Person type)
- Exact match on `entity_type`
- Embedding cosine similarity (from Qdrant, retrieved alongside ANN results)
- Shared alias check: does either entity have an alias matching the other's name?
- Co-occurrence bonus: do the two entities frequently appear in the same documents? (from `entity_relations` table)

#### Decision thresholds

| `match_probability` | Action |
|---|---|
| >= 0.85 | Auto-merge. Lower-`mention_count` entity is the loser; aliases and `entity_relations` transferred to winner via `POST /admin/entities/merge`. `graph_projected_at` nulled on winner to trigger FalkorDB re-projection. |
| 0.50 – 0.85 | Add to human review queue with context (source documents, co-occurrence evidence, embedding similarity). User confirms or denies merge. |
| < 0.50 | Add to known-distinct list. Pair is not re-evaluated unless an alias change triggers re-evaluation. |

#### Scheduling

Run as a weekly batch job (Sunday 02:00) via APScheduler. Also trigger on-demand via `POST /admin/entities/deduplicate` (admin-only, 202 response, background execution). The job produces a `deduplication_run` record in Postgres with `candidate_count`, `auto_merged`, `queued_for_review`, `duration_ms`.

#### Key decisions for implementation prompt

- Splink backend: use DuckDB for the matching computation (embedded, no separate service), write results to Postgres. Splink supports this natively.
- Model persistence: save Splink model to `/data/splink_model.json` after each training run
- Nickname table: start with Python `nicknames` library; allow user additions via a managed `nickname_mappings` table in Postgres (`canonical_name`, `variant_name`, `user_id`)
- Auto-merge safety: require `mention_count >= 2` on both entities before auto-merge. Single-mention entities are too uncertain for auto-merge.
- Tests: verify blocking candidate generation, score computation, auto-merge execution, known-distinct list, review queue population

---

### 5.3 Edge Quality Scoring

**Purpose:** Replace raw co-occurrence counts as the sole edge quality signal with a multi-signal composite score that filters spurious edges and reflects relationship strength more accurately.

#### PPMI scoring

Pointwise Mutual Information measures whether two entities co-occur more than expected under statistical independence:

```
PPMI(x,y) = max(0, log2(P(x,y) / (P(x) * P(y))))
```

A positive PPMI score indicates genuine association. Zero or negative values (clamped to 0 by PPMI) indicate coincidental co-occurrence.

For Lumogis, compute PPMI over the `entity_relations` table grouped by `evidence_id`. Store PPMI score as a property on RELATES_TO edges in FalkorDB (`ppmi_score` field).

- `P(x)` = count of documents containing entity x / total documents
- `P(x,y)` = count of documents containing both x and y / total documents

#### Window-level weighting

Sentence-level co-occurrence is more reliable than document-level. Weight co-occurrence contributions by granularity:

| Evidence granularity | Weight multiplier |
|---|---|
| Sentence-level | 1.0 |
| Paragraph-level | 0.7 |
| Document-level | 0.4 |

This requires `evidence_type` to carry granularity information — verify current values and add granularity metadata if not already present.

#### Composite edge score

```
edge_quality = w1 * normalised_frequency + w2 * ppmi_score + w3 * window_weight + w4 * temporal_decay_factor
```

Starting weights: `w1=0.25, w2=0.35, w3=0.20, w4=0.20`

Store as `edge_quality FLOAT` on RELATES_TO edges in FalkorDB. The CONTEXT_BUILDING injection and `query_graph` tool should filter on `edge_quality >= GRAPH_EDGE_QUALITY_THRESHOLD` (default 0.3) rather than just `co_occurrence_count`.

#### Key decisions for implementation prompt

- Compute PPMI in a scheduled weekly job using SQL aggregation over `entity_relations`. Write scores to a Postgres `edge_scores` table (`source_entity_id`, `target_entity_id`, `ppmi_score`, `computed_at`), then propagate to FalkorDB via reconciliation.
- Edge visibility threshold: edges with `edge_quality` below threshold are latent (stored but not returned in queries). Threshold is configurable via `GRAPH_EDGE_QUALITY_THRESHOLD`.
- Tests: verify PPMI computation, composite score formula, latent edge filtering, threshold configuration

---

### 5.4 Temporal Decay Engine

**Purpose:** Apply time-based decay to edge weights so that recent co-occurrences have more signal than historical ones. Prevent the graph from treating a two-year-old co-occurrence as equivalent to one from last week.

#### Decay model

Exponential decay:
```
decay_factor(t) = 0.5 ^ (days_since_last_evidence / half_life_days)
```

This can be updated incrementally — given a new observation, multiply the previous decayed weight by the decay factor and add the new observation weight. No need to store raw timestamps for every co-occurrence.

#### Per-edge-type half-lives

| Edge type | Recommended half-life | Rationale |
|---|---|---|
| DISCUSSED_IN | 30 days | Meeting and conversation context fades quickly |
| MENTIONS | 180 days | Document references remain relevant longer |
| RELATES_TO | 365 days | Semantic/conceptual relationships are more persistent |
| DERIVED_FROM | No decay | Provenance is structural, not time-sensitive |

Repeated co-occurrence resets and reinforces the decay clock. If two entities continue to co-occur, the effective weight reflects the ongoing relationship.

#### New FalkorDB edge properties

- `last_evidence_at` — TIMESTAMPTZ of most recent co-occurrence
- `decay_factor` — current 0.0-1.0 decay multiplier, recomputed weekly
- `edge_quality` — composite score incorporating decay

#### Key decisions for implementation prompt

- Half-life values: configurable via env vars `DECAY_HALF_LIFE_DISCUSSED_IN=30`, `DECAY_HALF_LIFE_MENTIONS=180`, `DECAY_HALF_LIFE_RELATES_TO=365`
- Decay job: runs as part of the weekly quality maintenance job, not a separate scheduler entry
- Edge pruning: edges with `edge_quality` below 0.05 after decay are flagged as dormant. Do not delete — retain for historical queries but exclude from active traversal.
- Tests: verify decay formula, half-life configuration, reinforcement on new co-occurrence, dormant edge flagging

---

### 5.5 Constraint Validation Layer

**Purpose:** Validate entity and edge data against type-specific rules on each ingestion batch. Catch quality regressions early before they propagate to the graph.

#### Constraint definitions

| Constraint | Rule |
|---|---|
| Person name required | Every Person entity must have a non-empty `name` property |
| Organisation name required | Every Organisation entity must have a non-empty `name` property |
| Document date required | Every Document entity should have an `ingested_at` or `created_at` date |
| No self-loops | MENTIONS edges must not have `source_id == target_id` |
| Valid edge types | Only defined edge types (MENTIONS, RELATES_TO, DISCUSSED_IN, DERIVED_FROM, WORKED_ON) are permitted |
| Orphan detection | Entities with zero edges after 7 days of existence are flagged |
| Alias uniqueness | No two distinct entities should share the same alias — flag as potential duplicate |
| Minimum completeness | Person entities should have at least one MENTIONS edge. Flag Person nodes with zero edges. |

#### Validation timing and output

Run constraint validation on each ingestion batch immediately after entity and relation creation. Violations are written to a `constraint_violations` Postgres table:

```sql
CREATE TABLE constraint_violations (
    violation_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id       UUID REFERENCES entities(entity_id),
    rule_name       TEXT NOT NULL,
    severity        TEXT NOT NULL, -- CRITICAL, WARNING, INFO
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);
```

- CRITICAL violations (self-loops, invalid edge types): trigger immediate logging
- WARNING violations (orphans, missing completeness): accumulate for weekly review queue

#### Key decisions for implementation prompt

- Severity levels: CRITICAL (data integrity), WARNING (quality signal), INFO (completeness signal)
- Resolution tracking: violations are marked resolved when the condition no longer holds
- Dashboard integration: constraint violation count is one of the six health metrics on the graph health dashboard
- Tests: verify each constraint rule, severity routing, violation creation, resolution detection

---

### 5.6 Graph Health Dashboard

**Purpose:** Surface six automated quality metrics in a dedicated dashboard view, giving the user visibility into graph health without requiring manual inspection of raw data.

#### Six health metrics

| Metric | Definition and target |
|---|---|
| Duplicate candidate count | Number of entity pairs in the review queue with `match_probability >= 0.50`. Target: decreasing over time. Alert if count exceeds 50 unreviewed pairs. |
| Orphan entity percentage | Entities with zero edges / total entities. Target: below 10%. Spikes indicate extraction pipeline issues. |
| Mean entity completeness | Average (filled required properties / expected required properties) per entity type. Target: above 70%. Calculated per `entity_type` to surface type-specific gaps. |
| Constraint violation count | Open violations in `constraint_violations` table by severity. Target: zero CRITICAL violations, WARNING count trending down. Displayed as breakdown by `rule_name`. |
| Ingestion quality trend | 7-day rolling average of `extraction_quality` scores for newly created entities. A declining trend indicates extraction quality regression. |
| Temporal freshness distribution | Histogram of entity `last-updated` timestamps bucketed by recency: last 7 days, 8-30 days, 31-90 days, 90+ days. |

#### Implementation

- New tab or section in the existing admin dashboard (`orchestrator/dashboard/index.html`)
- New `GET /admin/graph/health` endpoint — queries Postgres and returns a JSON summary
- All six metric queries run against Postgres only — no FalkorDB queries on the health endpoint
- Metrics are computed on request for simplicity. Add materialised view if query latency becomes an issue.

#### Key decisions for implementation prompt

- Alert thresholds configurable via env vars with sensible defaults
- Frontend renders using existing dashboard patterns — no new frontend framework
- Tests: verify each metric query returns correct values, endpoint auth, graceful handling of empty corpus

---

### 5.7 Human Review Queue

**Purpose:** Surface the top items requiring human attention in a prioritised, actionable queue. Primary interface between automated quality signals and user curation decisions.

#### Queue sources

- **Duplicate candidates:** entity pairs from Splink with `match_probability` 0.50-0.85, ranked by `(match_probability * combined_mention_count)`
- **Staged entities:** entities with `is_staged=TRUE`, ranked by `mention_count` descending
- **Drift flags:** entities with new surface forms differing significantly from known aliases
- **Constraint violations:** CRITICAL violations requiring attention, ranked by severity then age
- **Orphan entities:** entities with zero edges older than 7 days

#### Actions per queue item type

| Item type | Available actions |
|---|---|
| Duplicate candidate | Merge (designate winner/loser) / Keep separate (add to known-distinct list) / Skip |
| Staged entity | Promote to main graph / Discard / Edit name and promote |
| Drift flag | Confirm drift (create new alias, update canonical name) / Dismiss / Investigate |
| Constraint violation | Resolve manually / Suppress (mark as accepted exception) / Investigate |
| Orphan entity | Keep / Delete / Investigate source document |

#### API

- `GET /admin/review/queue` — returns paginated, prioritised list of review items with context
- `POST /admin/review/decide` — accepts `item_id`, `action`, and optional parameters
- All decisions logged to a `review_decisions` Postgres table for audit and model feedback

#### Weekly target

Surface no more than 15-20 items per weekly review session. If the queue exceeds this, automatically filter to the highest-priority items.

#### Key decisions for implementation prompt

- Priority scoring: `item_priority = severity_weight * (1 + centrality_score) * recency_factor`
- Centrality score: computed weekly from FalkorDB degree counts, stored in Postgres as `entity_centrality FLOAT`
- Decision feedback loop: Merge decisions update the alias table and trigger Splink model retraining flag. Discard decisions add to stop-entity list if appropriate.
- Tests: verify queue composition, priority ordering, action execution, decision logging, feedback loop triggers

---

### 5.8 Drift Detection Service

**Purpose:** Detect when entity references change in ways that may indicate concept drift — role changes, renames, organisational changes — and surface these for user review before they silently accumulate.

#### Detection heuristics

| Heuristic | Implementation |
|---|---|
| Surface form divergence | When a new extraction produces a name/alias for an existing entity with Jaro-Winkler distance below 0.85, flag for review. Example: "Head of Marketing" vs "VP Marketing" for the same person UUID. |
| Attribute conflict | When a new extraction contradicts an existing typed attribute (different job title, different organisation, different location), flag rather than auto-overwrite. Store both values in a `pending_attribute_updates` table for user decision. |
| Temporal gap | Entities not mentioned in any new document for more than `DRIFT_TEMPORAL_GAP_DAYS` (default: 90 days) are flagged as potentially inactive. |
| Neighbourhood change | Weekly: compare each entity's current RELATES_TO neighbour set to its neighbour set 4 weeks ago. If Jaccard similarity falls below 0.5, flag as significant neighbourhood change. |

#### Bi-temporal attribute storage

New Postgres table for attribute history:

```sql
CREATE TABLE entity_attribute_history (
    history_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID REFERENCES entities(entity_id),
    attribute_name      TEXT NOT NULL,
    attribute_value     TEXT NOT NULL,
    valid_from          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to            TIMESTAMPTZ,
    source_evidence_id  UUID,
    confidence          FLOAT
);
```

When a confirmed drift event occurs, the old attribute row gets a `valid_to` timestamp and a new row is created. Full history preserved without full temporal graph complexity.

#### Key decisions for implementation prompt

- Run drift detection as part of the weekly quality maintenance job
- Drift flags surface in the human review queue with existing and new values shown side by side
- Neighbourhood change detection requires FalkorDB to be available — degrade gracefully if unavailable
- Temporal gap detection runs against Postgres only
- Tests: verify surface form divergence detection, attribute conflict flagging, temporal gap detection, neighbourhood change computation

---

## 6. Operational Curation Workflow

### Automated (no user action required)

- **Every ingestion:** extraction post-processing, constraint validation, quality scoring
- **Every ingestion:** entity resolution tier 1 (deterministic exact+alias match)
- **Daily 03:00:** reconciliation job (M2) — self-heals FalkorDB projection
- **Weekly Sunday 02:00:** deduplication batch (Splink), edge quality scoring, temporal decay update, drift detection scan
- **Weekly:** health metrics refresh, review queue rebuild

### Weekly review session (15-25 minutes)

1. Open the graph health dashboard. Scan the six metrics. Note any alerts.
2. Open the review queue. Work through the top 10-15 items in priority order:
   - Duplicate candidates: merge or keep separate
   - Staged entities: promote or discard
   - Drift flags: confirm or dismiss
   - Constraint violations: resolve or suppress
3. Optional: run a spot-check query — "What do you know about [a frequently-mentioned person]?" and verify the ego network looks correct.
4. Optional: browse the ingestion quality trend. If declining, inspect recent source documents for changed characteristics.

### Monthly audit (10 minutes)

1. Review graph-level metrics trend over the past month. Is orphan percentage decreasing? Is mean completeness improving?
2. Sample 10 randomly selected recently-created entities. Spot-check quality — genuine named entities or noise?
3. Check the stop-entity list. Add any recurring noisy phrases observed in the sample.
4. Adjust thresholds if needed: `ENTITY_QUALITY_LOWER/UPPER`, `GRAPH_EDGE_QUALITY_THRESHOLD`, co-occurrence thresholds.

### Quarterly review (30 minutes)

1. Review the 20 entities with the most aliases — check for over-merging.
2. Review the 20 entities with the highest edge count — check for hub entities accumulating spurious connections.
3. Review the oldest unreviewed review queue items — clear the backlog.
4. Evaluate Splink model performance: inspect the last 50 auto-merge decisions. Were they correct?

---

## 7. Implementation Sequencing

The eight components are not equally urgent. The following sequence maximises quality improvement per implementation effort:

| Priority | Component | Rationale |
|---|---|---|
| 1 — Immediate | 5.1 Entity Extraction Post-Processor | Highest ROI. Stops noise at source. No schema changes beyond two new columns. Small, focused implementation. |
| 2 — Near-term | 5.5 Constraint Validation Layer | Cheap to implement. Provides immediate visibility into existing data quality. Required foundation for the health dashboard. |
| 3 — Near-term | 5.6 Graph Health Dashboard | Makes all quality signals visible. Motivates and informs all subsequent quality work. Depends on constraint validation for one of its six metrics. |
| 4 — Medium-term | 5.3 Edge Quality Scoring + 5.4 Temporal Decay | Implement together — they share a weekly job and write to the same edge properties. Improves query relevance immediately. |
| 5 — Medium-term | 5.2 Probabilistic Entity Resolution | Highest complexity. Depends on extraction post-processor being in place first (less noise = better Splink results). Needs a corpus of reasonable quality to train on. |
| 6 — Medium-term | 5.7 Human Review Queue | Depends on deduplication (5.2) and drift detection (5.8) to have items to surface. Implement once upstream components are producing items. |
| 7 — Later | 5.8 Drift Detection Service | Requires real data to tune heuristics. Implement after several months of real-world ingestion to understand actual drift patterns. |

> **Implementation principle:** Components 1-3 can each be implemented in a single Cursor session with a well-specified prompt. Components 4-6 each require 2-3 sessions. Component 7 (drift detection) should be deferred until the user has real operational data to calibrate against. Do not implement drift detection against synthetic or test data — the heuristic thresholds will be wrong.

---

## 8. Open Questions and Known Limits

### 8.1 Known gaps from the research review

- **Personal KG quality is under-researched.** The optimal precision/recall tradeoffs for a single-user, continuously-ingested graph have not been empirically validated. The thresholds in this document are informed by the literature and first principles but will need empirical tuning.
- **spaCy confidence calibration is a practical blocker.** spaCy's NER model does not produce well-calibrated confidence scores in recent versions. The composite quality score compensates with structural signals, but NER confidence remains a weak signal. If spaCy is replaced or augmented with a fine-tuned spancat model, the composite score weights should be revisited.
- **Drift versus noise discrimination is unsolved in the literature.** When a new extraction contradicts an existing entity attribute, it is genuinely unclear whether this is concept drift (the entity changed) or extraction noise (the extraction is wrong). The drift detection service takes a conservative approach — flag both, let the user decide.
- **Embedding-based drift detection is immature** and explicitly flagged as problematic in recent literature (Verkijk et al., 2023). Do not invest in embedding-based drift detection at this stage.

### 8.2 Design decisions to revisit after real data

- **Extraction quality thresholds** (`ENTITY_QUALITY_LOWER=0.35`, `ENTITY_QUALITY_UPPER=0.60`): tune after 4-8 weeks of real-world ingestion. Review the proportion of entities landing in each tier.
- **Splink auto-merge threshold (0.85):** conservative by design. After reviewing the first batch of auto-merge decisions, adjust if precision is too low or recall is too conservative.
- **Temporal decay half-lives:** recommended values are based on general KG literature. Personal KG relationships may decay faster or slower depending on the user's domain. Treat these as starting points.
- **Review queue size target (15-20 items per session):** may need adjustment based on actual queue growth rates. If the queue grows faster than the user can review, raise the auto-merge threshold to reduce manual burden.

### 8.3 Out of scope for this strategy

- **Typed relation extraction (REBEL/OpenNRE):** moving from co-occurrence-based RELATES_TO edges to typed relations ("works at", "reports to") is a significant capability uplift requiring a local LLM or domain-specific training data. Defer until Phase 5 or when local LLM quality improves sufficiently.
- **Community/topic detection algorithms:** excluded from Phase 3 plan scope.
- **Multi-user graph isolation:** Phase 6. Current single-user model is not impacted by any quality component described here.
- **External knowledge base linking:** entity-fishing and similar tools link entities to Wikidata/DBpedia. Conflicts with Lumogis's privacy-first, local-only design. Not recommended.

---

*Research basis: comprehensive literature review covering Fellegi-Sunter (1969), Zaveri et al. (2016), Xue and Zou (2022), Noy et al. (2019), Rasmussen et al./Graphiti (2025), and 40+ additional sources across entity resolution, edge quality, KG quality frameworks, and concept drift.*
