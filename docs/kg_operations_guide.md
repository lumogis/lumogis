# Lumogis Knowledge Graph — Operations Guide

> A practical guide for managing and maintaining the Lumogis knowledge graph. Written for both technical and non-technical users.

---

## Multi-user, authentication, and graph URLs

- **`AUTH_ENABLED=false` (default on many dev stacks):** The app behaves like a single-user install. FastAPI auth dependencies synthesise a **synthetic `user_id` of `"default"`** with an admin role, so you can use browser pages and `curl` without a bearer token, matching older documentation.
- **`AUTH_ENABLED=true` (family-LAN / multi-user):** **Bearer JWT** (from `/api/v1/auth/login` or the web UI session) is required for routes the auth middleware protects. The graph visualization APIs (`/graph/ego`, `/graph/search`, etc.) are **scoped to the JWT `sub`**. The Knowledge Graph **management** surface (`/graph/mgm` and the `/kg/*` operator APIs on Core) and **read/list review surfaces** (e.g. `GET /review-queue`) are **admin-only** when auth is on. `POST /review-queue/decide` is available to the **owning user or an admin** (see `orchestrator/routes/admin.py`).

**`GET /graph/health` (Postgres quality metrics):** The implementation still aggregates metrics for **`user_id = 'default'`** only (operator dashboard / god-mode read). In a multi-user household, treat these numbers as **indicative for the legacy bucket**, not a full per-user report—unless your deployment still uses `"default"`.

**`GRAPH_MODE=service`:** The in-process graph plugin does **not** register its HTTP router on Core. Graph read APIs and `/graph/viz` are served from the **`lumogis-graph` process** at `KG_SERVICE_URL` (default `http://lumogis-graph:8001` inside Docker). The HTML management page is still at **`/graph/mgm` on Core** (admin) and is mirrored on the KG service as `/mgm`. To open viz from the host, add a **host port** for 8001 in your compose overlay or `docker compose exec` to curl the KG container.

**`[Graph]` lines in chat:** In `GRAPH_MODE=inprocess`, the `CONTEXT_BUILDING` path still uses the **`"default"`** user bucket for graph context (hook payload). **Service mode** currently calls KG `/context` with the real `user_id`, but the KG `on_context_building` copy still hard-codes `"default"` until a follow-up wires `user_id` through—so **households should not expect per-user graph injection in chat to match the JWT yet.**

---

## Table of Contents

1. [What the Knowledge Graph Does](#1-what-the-knowledge-graph-does)
2. [What Gets Extracted and Why](#2-what-gets-extracted-and-why)
3. [Using the Visualization](#3-using-the-visualization)
4. [The Review Queue — Weekly Workflow](#4-the-review-queue--weekly-workflow)
5. [The Graph Health Dashboard](#5-the-graph-health-dashboard)
6. [Managing Entity Quality](#6-managing-entity-quality)
7. [The Weekly Automated Job](#7-the-weekly-automated-job)
8. [Troubleshooting](#8-troubleshooting)
9. [What Is Coming Next](#9-what-is-coming-next)
10. [Where the Graph Runs (Deployment Modes)](#10-where-the-graph-runs-deployment-modes)

---

## 1. What the Knowledge Graph Does

### What problem it solves

When you use Lumogis over weeks and months — chatting, uploading documents, taking notes — it accumulates a large amount of information. The knowledge graph connects all of that information together. It tracks the people, organisations, projects, and concepts you interact with, and remembers which ones are related to each other and where each one was mentioned.

Without the graph, Lumogis can search your documents by keywords. With the graph, Lumogis understands relationships: it knows that Alice works on Project Phoenix, that Project Phoenix was discussed in Tuesday's session, and that the session also mentioned Acme Corp. When you ask about Alice, Lumogis can surface these connections automatically.

### What you experience when it is working well

- When you chat with Lumogis and mention a person or project, the response includes relevant context from previous conversations and documents — even if you did not search for them explicitly.
- The graph visualization shows a clear, navigable map of the people, projects, and concepts in your world.
- Duplicate entities are automatically detected and either merged or flagged for your review.
- Noise entities (like "the meeting" or "this project") are filtered out before they clutter the graph.

### What you experience when it is not working well

- Chat responses miss obvious connections ("I told you about this last week").
- The visualization is cluttered with vague entries like "the client" or "the project".
- Important people or organisations appear multiple times under slightly different names.
- The graph shows stale information that was relevant months ago but not anymore.

All of these problems can be addressed using the tools described in this guide.

---

## 2. What Gets Extracted and Why

### Entities

An **entity** is a named thing that Lumogis identifies in your conversations and documents. There are four types:

| Type | Examples |
|------|----------|
| **Person** | Ada Lovelace, Dr. Müller, your colleague Sarah |
| **Organisation** | Acme Corp, Bundesamt für Statistik, the WHO |
| **Project** | Project Phoenix, Website Redesign, Q3 Budget |
| **Concept** | Machine Learning, Compliance Framework, Agile |

**Good entities** are specific and meaningful: "Ada Lovelace", "Project Phoenix", "Bundesamt für Statistik".

**Noisy entities** are vague or generic: "the meeting", "the client", "next steps", "the project". Lumogis has a built-in list of 160+ such phrases that are automatically filtered out. You can add more (see [Managing Entity Quality](#6-managing-entity-quality)).

### Edges (relationships)

Edges connect entities to each other and to their sources:

- **MENTIONS**: Links a document, session, note, or audio memo to an entity it contains. This is how Lumogis tracks provenance — where each entity was first seen and where it has been discussed.
- **RELATES_TO**: Links two entities that frequently appear together. If Alice and Project Phoenix are both mentioned in the same session three or more times, they get a RELATES_TO connection. The strength of this connection grows each time they co-occur.
- **DISCUSSED_IN**: Links an entity to a chat session where it was discussed.

### Why some entities are "staged"

When Lumogis extracts an entity, it scores the quality of that extraction. Entities that score well are added to the graph immediately. Entities that score in the middle range — not clearly noise, but not confidently correct either — are **staged**.

A staged entity exists in the database but is hidden from the graph and from chat context. It will be promoted to a full entity automatically when:
- It is mentioned again and the new mention has a higher quality score, or
- It accumulates enough mentions (3 by default) to prove it is worth keeping.

You can also manually promote or discard staged entities through the review queue.

---

## 3. Using the Visualization

### How to access it

With **`GRAPH_MODE=inprocess`**, open:

```
http://localhost:8000/graph/viz
```

With **`GRAPH_MODE=service`**, Core does not mount the plugin’s `/graph/*` routes; use the **KG service** (same path), e.g. from another container:

```
http://lumogis-graph:8001/graph/viz
```

—or publish port **8001** to the host and use `http://localhost:8001/graph/viz`. The page is a standalone Cytoscape.js app that loads data from the graph JSON APIs.
When **`AUTH_ENABLED=true`**, open the page **while logged in** (or pass `Authorization: Bearer <access_token>` to the JSON APIs) so results are **scoped to your user** and, where applicable, **visibility rules** (personal / shared / system) from ADR-015.

### What the search does

The search box at the top lets you find entities by name. Start typing at least 2 characters and the autocomplete will show matching entities, sorted by how often they have been mentioned. Select an entity to center the graph on it.

### Query types

**Ego network** — The default view. Shows a single entity at the center with all its direct connections. This answers the question: "What is connected to this entity?" The center entity is highlighted, and each connection shows the co-occurrence strength (how many times the two entities appeared together).

**Path** — Shows the shortest connection between two entities. Enter a start and end entity, and the visualization draws the chain of relationships between them. This answers: "How are these two things connected?" The maximum path length is 4 hops.

**Mentions** — Shows which documents, sessions, notes, or audio memos mention a given entity. This answers: "Where did this entity come from?" Results are ordered by most recent first.

### How to interpret the visualization

- **Node size** reflects mention count — bigger nodes are mentioned more often.
- **Node color** reflects entity type:
  - Person, Organisation, Project, and Concept each have distinct colors.
  - Document, Session, Note, and AudioMemo nodes (information sources) appear when using the mentions query.
- **Edge thickness** reflects co-occurrence strength — thicker lines mean stronger relationships.
- **Center nodes** are highlighted to show the entity you searched for.

---

## 4. The Review Queue — Weekly Workflow

### What the review queue is

The review queue collects items that need your attention. It surfaces problems and ambiguities that Lumogis cannot resolve automatically, and presents them in priority order so you can work through them efficiently. **`GET /review-queue` and `?source=all` are admin-only** when `AUTH_ENABLED=true`. **`POST /review-queue/decide`** requires an **authenticated** user: non-admins may act only on their own items; **admins** can act for the household. Items carry **`user_id` and `scope`** for attribution in the admin UI.

### The five item types

| Item Type | What It Means | Priority |
|-----------|--------------|----------|
| **Ambiguous entity** | Two entities share a name but only overlap on one topic tag. Lumogis cannot tell if they are the same thing or different things. | Highest (1.0) |
| **Constraint violation** | A critical data quality issue — for example, a Person entity with no name, or a relation with an invalid type. | High (0.9) |
| **Staged entity** | An entity that scored in the uncertain range during extraction. It is waiting for you to promote it (add to graph) or discard it. | Medium (0.7) |
| **Orphan entity** | An entity that has existed for more than 7 days but has no connections to any other entity. It may be noise, or it may have lost its connections. | Low (0.5) |
| **Dedup candidate** | Two entities that look like they might be duplicates (based on name similarity, embedding closeness, and shared aliases). The system was not confident enough to merge them automatically. | Appears as ambiguous entity |

### How to action each type

#### Ambiguous entity → `merge` or `distinct`

Open the item. You will see two entities side by side with their names, types, and the reason they were flagged (usually "1 context_tag overlap").

- **Merge**: If they are the same entity (e.g. "Dr. Sarah Mueller" and "S. Mueller"), choose merge. The first entity (candidate A) becomes the winner — all mentions, aliases, and connections from the second entity are transferred to it, and the second entity is deleted.
- **Keep separate**: If they are genuinely different entities (e.g. "Cambridge" the university and "Cambridge" the city), choose distinct. The pair is recorded as intentionally separate, and the system will not flag them again.

#### Staged entity → `promote` or `discard`

You will see the entity name, type, quality score, and mention count.

- **Promote**: If the entity looks correct and meaningful, promote it. It will immediately appear in the graph and start participating in co-occurrence analysis.
- **Discard**: If the entity is noise or an extraction error, discard it. It is permanently deleted.

#### Constraint violation → `suppress`

You will see the rule name, severity, and detail. Most CRITICAL violations indicate data wiring errors that were caught by automated checks. If you review the violation and determine it is a false positive or has been addressed externally, suppress it. This marks it as resolved.

#### Orphan entity → `dismiss`

An orphan entity has no evidence connections. If you recognise it as valid, dismiss the warning — the entity stays in the graph and will gain connections if mentioned again. If it looks like noise, you can separately delete it via the merge endpoint.

### How long a weekly session should take

For a typical personal knowledge base (50–500 entities), the review queue should take 5–15 minutes per week. Most items are quick decisions once you see the entity names side by side.

### What happens if you skip weeks

Nothing breaks. Items accumulate in the queue and are presented in priority order when you return. Staged entities continue to be promoted automatically when they accumulate enough mentions. The automated deduplication job handles the most obvious duplicates without your input.

However, skipping many weeks means:
- The graph may contain duplicates that reduce the quality of chat context injection.
- Noise entities in the staging area are not cleaned up (they remain hidden from the graph, so the impact is low).

---

## 5. The Graph Health Dashboard

The Graph Health tab in the Lumogis dashboard shows six metrics. Access it at:

```
http://localhost:8000/dashboard
```

Then click the **Graph Health** tab. When **`AUTH_ENABLED=true`**, the dashboard request must be **authenticated**; **`GET /graph/health` itself** also returns **401** without a valid bearer. Remember the metrics are still computed for **`user_id = 'default'`** in the backend (see the introduction above)—they do not yet sum every household user.

### What each metric means

#### 1. Duplicate candidate count

The number of items currently in the review queue. This includes ambiguous entity pairs, constraint violations, staged entities, and orphan entities.

- **Good value**: 0–10. Means the graph is well-maintained.
- **Concerning value**: 50+. Means the queue is growing faster than you are reviewing it. See [Troubleshooting](#review-queue-growing-faster-than-reviews).

#### 2. Orphan entity percentage

The percentage of non-staged entities that have zero evidence connections (no MENTIONS edges) and have existed for more than 7 days.

- **Good value**: Below 5%. Most entities should be connected to at least one document or session.
- **Concerning value**: Above 20%. May indicate an extraction problem or data loss.
- **What to do**: Check the review queue for orphan entities. Dismiss or delete the ones that are clearly noise.

#### 3. Mean entity completeness

The average `extraction_quality` score across all non-staged entities. This reflects how confidently the system extracted your entities.

- **Good value**: 0.65 or above. Means most entities have clear names, proper capitalisation, and relevant context tags.
- **Low value**: Below 0.50. May indicate that the extraction model is struggling with your content type. Consider reviewing the stop entity list or adjusting quality thresholds.

#### 4. Constraint violation counts

A breakdown of open violations by severity:

- **CRITICAL**: Data integrity issues that should be investigated (e.g. an entity with no name, a self-referencing relation).
- **WARNING**: Quality concerns like orphan entities or shared aliases.
- **INFO**: Completeness hints, such as a Person entity with no evidence edges.

**Good value**: Zero CRITICAL violations. A few WARNING/INFO violations are normal.

#### 5. Ingestion quality trend (7 days)

The average extraction quality of entities created in the last 7 days. This tells you whether recent extractions are higher or lower quality than the historical average.

- **Good**: Close to or above `mean_entity_completeness`.
- **Dropping**: If this is significantly below the overall mean, recent content may be harder to extract entities from, or the extraction model may need attention.
- **Null**: No entities were created in the last 7 days.

#### 6. Temporal freshness

A histogram showing how recently entities were last updated:

| Bucket | Meaning |
|--------|---------|
| Last 7 days | Actively used entities |
| 8–30 days | Recently relevant entities |
| 31–90 days | Aging entities |
| 90+ days | Potentially stale entities |

**Good distribution**: Most entities in the first two buckets. A healthy, active graph has frequent updates.

**Concerning**: Most entities in the 90+ day bucket. May indicate the graph is not being fed new content.

---

## 6. Managing Entity Quality

### How to add phrases to the stop entity list

The stop entity list prevents specific phrases from ever entering the graph. It is stored at:

```
orchestrator/config/stop_entities.txt
```

To add a new phrase:

1. Open the file in any text editor.
2. Add one phrase per line. Lines starting with `#` are comments.
3. Save the file. The change takes effect on the next entity extraction — no restart required.

Example: if you keep seeing "the stakeholder group" appear as an entity, add it to the list:

```
the stakeholder group
```

Matching is case-insensitive. "The Stakeholder Group" and "THE STAKEHOLDER GROUP" will both be blocked.

### How thresholds work and when to adjust them

Two thresholds control entity routing:

- **Lower threshold** (`ENTITY_QUALITY_LOWER`, default 0.35): Entities scoring below this are permanently discarded. Raise this if you see too much noise getting through. Lower it if important entities with unusual names are being lost.
- **Upper threshold** (`ENTITY_QUALITY_UPPER`, default 0.60): Entities scoring between lower and upper are staged. Entities at or above upper go directly into the graph. Raise this if too many uncertain entities are cluttering the graph. Lower it if too many good entities are getting stuck in staging.

To change them, set the environment variables in your `.env` file and restart:

```
ENTITY_QUALITY_LOWER=0.40
ENTITY_QUALITY_UPPER=0.65
```

### What to do when the graph contains obvious duplicates

If you notice two entities that should be one (e.g. "Acme Corp" and "ACME Corporation"):

**Option 1: Use the review queue**. If a deduplication candidate already exists, decide "merge" from the queue.

**Option 2: Manual merge**. Call the merge API directly (**admin** when `AUTH_ENABLED=true`):

```bash
curl -X POST http://localhost:8000/entities/merge \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"winner_id": "<UUID-of-entity-to-keep>", "loser_id": "<UUID-of-entity-to-remove>", "user_id": "<owning-user-id>"}'
```

Omit the `Authorization` header when `AUTH_ENABLED=false`. The body **`user_id`** is the **data owner** of the entities (not optional in real multi-user use—defaults to `"default"` in the Pydantic model for backward compatibility).

The winner keeps its name and absorbs the loser's aliases, mentions, context tags, and mention count. The loser is permanently deleted.

**Option 3: Trigger deduplication**. Run the full deduplication pipeline to find and handle all duplicates (**admin** when `AUTH_ENABLED=true`):

```bash
curl -X POST http://localhost:8000/entities/deduplicate \
  -H "Authorization: Bearer <access_token>"
```

This returns a 202 with a `run_id`. The job runs in the background. (The in-process job currently targets the **`"default"`** user bucket; broader per-user runs are a follow-up for multi-tenant quality jobs.)

### What to do when important entities are missing

If an entity you expect to see is not in the graph:

1. **Check if it is staged**: Look in the review queue for staged entities. It may be waiting for promotion.
2. **Check the stop list**: The entity name might match a stop phrase. Check `orchestrator/config/stop_entities.txt`.
3. **Check the quality thresholds**: If the entity has an unusual name (single word, all lowercase, etc.), it may be scoring below the lower threshold. Try lowering `ENTITY_QUALITY_LOWER` temporarily.
4. **Mention it again**: If the entity was previously discarded, mentioning it in a new conversation or document will re-extract it. With enough mentions, even a staged entity gets promoted automatically.

---

## 7. The Weekly Automated Job

### What it does without any user action

Every Sunday at 02:00 UTC (configurable), Lumogis runs a maintenance job that:

1. **Scores all entity pair relationships** — Calculates how strongly each pair of entities is connected based on how often they co-occur, how recently they were seen together, and how precise the evidence is. Updates both the Postgres database and the FalkorDB graph with these scores.

2. **Checks for orphan entities** — Finds entities that have existed for more than 7 days but have zero connections. Flags them in the review queue as warnings.

3. **Checks for alias conflicts** — Finds cases where two different entities share the same alias. Flags them for review.

4. **Runs probabilistic deduplication** — Uses Splink (a record-linking library) to find entities that are likely duplicates based on name similarity, embedding closeness, and shared aliases. High-confidence duplicates (score >= 0.85, both with 2+ mentions) are merged automatically. Medium-confidence pairs (0.50–0.85) are added to the review queue.

### When it runs

- **Default**: Sunday at 02:00 UTC
- **Configurable**: Set `DEDUP_CRON_HOUR_UTC` in `.env` to change the hour (0–23)
- **Separate daily job**: The graph reconciliation (replaying missed projections into FalkorDB) runs daily at 03:00 UTC, independent of the weekly job

### How to trigger it manually

```bash
curl -X POST http://localhost:8000/entities/deduplicate \
  -H "Authorization: Bearer <access_token>"
```

(Omit `Authorization` when `AUTH_ENABLED=false`.) This triggers just the deduplication step (for the **`"default"`** user id in the current implementation). For the full weekly pipeline including edge scoring and constraint checks, use **`POST /kg/trigger-weekly`** (admin) or wait for the schedule. If you need to run it immediately, restart the container and it will run at the next scheduled time.

### How to check if it ran successfully

1. **Check the dashboard**: Open Graph Health and look at the metrics. If scores were computed, you will see non-zero values for constraint violations and temporal freshness.

2. **Check the logs**: The job logs a structured summary:

```
component=quality_maintenance pairs_computed=42 pairs_upserted=42 orphan_violations=3 alias_violations=0 auto_merged=1 queued_for_review=2 duration_ms=1234
```

3. **Check deduplication runs**: Query the database directly:

```sql
SELECT run_id, started_at, finished_at, candidate_count, auto_merged, queued_for_review, error_message
FROM deduplication_runs
ORDER BY started_at DESC
LIMIT 5;
```

A successful run has `finished_at IS NOT NULL` and `error_message IS NULL`.

---

## 8. Troubleshooting

### Where to look first

Before changing anything, check which deployment mode you are in. The `GRAPH_MODE` setting in `.env` selects how the knowledge graph runs:

- `inprocess` (default) — the graph plugin runs inside the orchestrator container. Webhooks, weekly jobs, and chat-context injection all happen in the same process.
- `service` — the graph runs in a separate `lumogis-graph` container. The orchestrator sends events to it over HTTP.
- `disabled` — no graph anywhere.

Most of the troubleshooting below is mode-agnostic, but some symptoms only appear in `service` mode. See [§10 Where the Graph Runs](#10-where-the-graph-runs-deployment-modes) for the mode-specific failure paths.

### FalkorDB is unavailable: what still works, what does not

**Still works:**
- Document ingestion, search, and chat function normally
- Entity extraction continues and writes to Postgres and Qdrant
- The dashboard and all admin endpoints work
- Graph health metrics work (they query Postgres, not FalkorDB)

**Does not work:**
- The graph visualization page shows "FalkorDB graph is not available"
- The `query_graph` tool returns "Graph backend is not configured"
- Chat responses do not include `[Graph]` context lines
- Graph backfill and reconciliation skip without error

**What to do:** Check that FalkorDB is running (`docker compose -f docker-compose.falkordb.yml ps`) and that `GRAPH_BACKEND=falkordb` and `FALKORDB_URL=redis://falkordb:6379` are set in your `.env`. Restart the orchestrator after changing `.env`.

### Graph looks stale: how to force a refresh

If the graph visualization is missing recent entities or connections:

1. **Trigger a backfill**:

```bash
curl -X POST http://localhost:8000/graph/backfill \
  -H "Authorization: Bearer <access_token>" \
  -H "X-Graph-Admin-Token: <your-token>"
```

`POST /graph/backfill` uses **`_check_admin`**, not `require_admin`: when `AUTH_ENABLED=true` you need a **valid JWT** (any authenticated user). When `GRAPH_ADMIN_TOKEN` is set, you must also send the matching **`X-Graph-Admin-Token`**. Omit `Authorization` when `AUTH_ENABLED=false`. Omit `X-Graph-Admin-Token` if `GRAPH_ADMIN_TOKEN` is unset.

This replays all Postgres rows that are newer than their last graph projection into FalkorDB. It runs in the background and processes all stale rows.

2. **Check if reconciliation is running**: The daily reconciliation job runs at 03:00 UTC. If you added data recently, wait until after 03:00 or trigger a manual backfill.

3. **Check FalkorDB connectivity**: If FalkorDB was down when entities were created, the `graph_projected_at` column will be NULL and reconciliation will pick them up on its next run.

### Too many noise entities in the graph: what to adjust

1. **Add phrases to the stop list**: Open `orchestrator/config/stop_entities.txt` and add the noise phrases you are seeing. No restart needed.

2. **Raise the lower quality threshold**: Set `ENTITY_QUALITY_LOWER=0.40` or higher in `.env` and restart. This discards more borderline entities.

3. **Raise the upper quality threshold**: Set `ENTITY_QUALITY_UPPER=0.65` or higher. This stages more entities for review instead of adding them to the graph directly.

4. **Review staged entities**: Go to the review queue and discard any staged entities that are noise.

### Important entity keeps getting staged: what to check

If a specific entity keeps getting staged instead of being added to the graph:

1. **Check its quality score**: The extraction quality is based on name characteristics. Single-word, all-lowercase names score lower. Names starting with "the" or "a" score lower.

2. **Mention it more**: Each additional mention increments the mention count. At 3 mentions (configurable via `ENTITY_PROMOTE_ON_MENTION_COUNT`), a staged entity is automatically promoted.

3. **Lower the upper threshold**: If many legitimate entities are getting staged, lower `ENTITY_QUALITY_UPPER` to 0.55 or 0.50.

4. **Promote manually**: Go to the review queue, find the staged entity, and promote it directly.

### Duplicate entities appearing: how to find and merge them

1. **Check the review queue**: Dedup candidates appear as ambiguous entity items. Merge or mark as distinct.

2. **Run deduplication manually**:

```bash
curl -X POST http://localhost:8000/entities/deduplicate \
  -H "Authorization: Bearer <access_token>"
```

3. **Merge specific pairs manually** (admin; include `user_id` in the body for multi-user):

```bash
curl -X POST http://localhost:8000/entities/merge \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"winner_id": "<keep-this-uuid>", "loser_id": "<delete-this-uuid>", "user_id": "<owning-user-id>"}'
```

4. **Search for the entities**: Use the visualization search or query Postgres directly to find entity UUIDs:

```sql
-- Replace the user_id with the owning account (JWT `sub`) in multi-user installs.
-- Use … IN (SELECT id FROM users) to explore, or the admin data export, if you
-- are unsure of the id string.
SELECT entity_id, name, entity_type, mention_count, user_id, scope
FROM entities
WHERE lower(name) LIKE '%acme%' AND user_id = '<your-user-id>';
```

### Review queue growing faster than reviews: how to manage

If the queue consistently grows between your review sessions:

1. **Run deduplication more aggressively**: The auto-merge threshold is 0.85 by default. Entities with fewer than 2 mentions are never auto-merged, which is conservative. The weekly job handles the most obvious cases.

2. **Increase the lower quality threshold**: Raising `ENTITY_QUALITY_LOWER` from 0.35 to 0.45 discards more borderline entities before they ever enter the review queue.

3. **Add stop phrases**: Many ambiguous entity items are caused by vague phrases that should have been filtered. Add them to the stop list.

4. **Process the queue in batches**: Focus on the highest-priority items first (ambiguous entities and constraint violations). Staged entities and orphan entities are lower priority and have less impact on graph quality.

5. **Dismiss orphan entities in bulk**: If you have many orphan entity warnings and you are comfortable that they are noise, dismiss them all to clear the queue.

---

## 9. What Is Coming Next

### M5 — Quick Capture

A fast note-taking interface that lets you capture thoughts, meeting notes, and ideas directly into Lumogis without starting a full chat session. Quick capture notes are processed through the same entity extraction pipeline and appear as `Note` nodes in the graph.

### M6 — Vault Adapter

Integration with file-based knowledge management tools (like Obsidian). The vault adapter will watch a folder of markdown files, extract entities and relationships, and materialise internal links (`LINKS_TO` edges) and tags (`TAGGED_WITH` edges) in the graph. This turns your existing notes into a connected knowledge graph without re-typing anything.

### M7 — Audio

Voice memo support. Record audio memos that are automatically transcribed (via Whisper), processed through entity extraction, and connected to the graph as `AudioMemo` nodes. The `DERIVED_FROM` edge type (already reserved in the schema) will link audio memos to their transcript documents.

### Drift Detection

Automated monitoring of graph quality metrics over time. Drift detection will track whether entity quality, edge scores, orphan rates, and constraint violations are improving or degrading, and alert you when something needs attention. This builds on top of the graph health metrics that are already being collected.

### What will improve when each is built

- **M5**: Faster data capture means more entities and connections, which improves graph density and chat context quality.
- **M6**: Existing notes become part of the graph, dramatically increasing coverage without any new data entry. Internal links create high-quality, human-verified edges.
- **M7**: Voice memos become searchable and connected. Entities mentioned in passing during voice notes are captured and linked.
- **Drift detection**: Early warning when graph quality degrades, so problems are caught before they affect your day-to-day experience.

---

## 10. Where the Graph Runs (Deployment Modes)

Lumogis ships two equivalent ways of running the knowledge graph. They produce the same data and expose the same operator experience — only the process boundary differs. The choice is made by the `GRAPH_MODE` environment variable in `.env`:

| Mode | What runs where | When to choose it |
|------|-----------------|-------------------|
| `inprocess` (default) | The graph plugin lives inside the orchestrator container. One process, one set of logs, one image to update. | Single-machine self-hosted setups, dev work, anything you don't need to scale or isolate. |
| `service` | The plugin runs in a separate `lumogis-graph` container. Orchestrator talks to it over HTTP. Both processes share the same Postgres, Qdrant, and FalkorDB. | You want to update / restart the graph without restarting chat, run it on a different host, give it its own resource budget, or run the KG as an optional isolated service you can enable or disable independently. |
| `disabled` | No graph code runs anywhere. `query_graph` is not available, no `[Graph]` lines appear in chat, nothing is projected to FalkorDB. | You want to turn the feature off entirely without removing the database. |

### Switching modes

1. Edit `.env` and set `GRAPH_MODE=inprocess` or `GRAPH_MODE=service`.
2. If switching to `service`, also set `GRAPH_WEBHOOK_SECRET` to a long random string and bring up the **`lumogis-graph` overlay** (`docker-compose.premium.yml` — historical filename):

   ```bash
   docker compose -f docker-compose.yml \
     -f docker-compose.falkordb.yml \
     -f docker-compose.premium.yml up -d
   ```

3. If switching back to `inprocess`, `docker compose down lumogis-graph` is enough — the orchestrator picks up the new mode at next restart.

The graph data itself is not affected by the switch: both modes read and write the same Postgres rows and the same FalkorDB graph. You can swap modes without losing entities, edges, or settings.

### Verifying both modes produce the same graph

A regression test in the repository — `make test-graph-parity` — runs the same fixture set through both modes and diffs the resulting FalkorDB state. It is intended for developers and CI; operators do not need to run it routinely. The test wipes the named Docker volumes for Postgres, FalkorDB, and Qdrant between phases, so do NOT run it against a stack that holds real data without backing up first.

### Service-mode-only failure modes

These only apply when `GRAPH_MODE=service`. In `inprocess` mode none of these can happen because there is no network call.

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Chat responses suddenly stop including `[Graph]` lines (extraction still works, graph viz still works) | Core's 40 ms `/context` timeout is firing. Either the KG container is overloaded or FalkorDB is slow. | Check `docker compose logs lumogis-graph` for `/context: in-route work exceeded 35 ms budget` warnings. Restart the KG container; if persistent, increase its memory limit (`KG_MEM_LIMIT`). |
| All webhooks return 503 in KG logs | `GRAPH_WEBHOOK_SECRET` is unset AND `KG_ALLOW_INSECURE_WEBHOOKS=false`. | Set a `GRAPH_WEBHOOK_SECRET` in `.env` for both Core and KG (one shared secret), or set `KG_ALLOW_INSECURE_WEBHOOKS=true` on the KG container for a trusted dev LAN only. |
| All webhooks return 401 | The two processes have different `GRAPH_WEBHOOK_SECRET` values, or one is set and the other is not. | Make sure both Core and KG read the same `.env` (or set the variable identically in both compose files). |
| Newly ingested entities are missing from the graph for a few hours | The KG container was unreachable when the events fired. Reconciliation will catch them up. | Wait until 03:00 UTC, or trigger an immediate catch-up: `curl -X POST http://localhost:8000/graph/backfill` (uses Core's reverse-proxy path) or `curl -X POST http://lumogis-graph:8001/graph/backfill` (direct, only available when the KG host port is exposed). |
| Weekly job log line appears in BOTH the orchestrator and the KG container | Both schedulers are active. | In `service` mode, leave the KG scheduler enabled (`KG_SCHEDULER_ENABLED=true`) and make sure the Core process is in `service` mode too — Core skips the weekly job in that mode. If you want to silence the KG scheduler instead (e.g. running multiple KG replicas), set `KG_SCHEDULER_ENABLED=false` on all but one. |
| `/graph/mgm` page is unreachable on the KG container's host port | The **`lumogis-graph` compose overlay** does NOT expose port 8001 to the host by default — that is intentional. | Reach `/mgm` via Core's `/graph/mgm` (which is the same page), or `docker compose exec lumogis-graph curl http://localhost:8001/mgm`, or add a port mapping in your own overlay file. |
