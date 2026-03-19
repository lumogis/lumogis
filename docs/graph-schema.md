# Lumogis Graph Schema

This schema defines the graph structure for knowledge graph plugins. lumogis does not write to the graph — it ships this schema so community implementations can target a consistent structure. See `ports/graph_store.py` for the protocol interface.

---

## Graph Name

`lumogis`

---

## Node Types

All nodes carry a `lumogis_id` property that references the corresponding UUID in Postgres (`entities.entity_id`), enabling cross-store consistency checks.

### Person

Represents a human individual mentioned across sessions or documents.

| Property | Type | Required | Notes |
|---|---|---|---|
| `lumogis_id` | String (UUID) | Yes | FK to `entities.entity_id` |
| `name` | String | Yes | Canonical name in original language |
| `aliases` | String[] | No | Alternative names seen |
| `context_tags` | String[] | No | Domain/topic tags from extraction |
| `mention_count` | Integer | Yes | Denormalized from Postgres for fast graph queries |
| `user_id` | String | Yes | Owner scope |
| `created_at` | String (ISO 8601) | Yes | |
| `updated_at` | String (ISO 8601) | Yes | |

### Organisation

Represents a company, institution, government body, or other named organisation.

| Property | Type | Required | Notes |
|---|---|---|---|
| `lumogis_id` | String (UUID) | Yes | FK to `entities.entity_id` |
| `name` | String | Yes | Official name in original language |
| `aliases` | String[] | No | Trade names, abbreviations |
| `context_tags` | String[] | No | |
| `mention_count` | Integer | Yes | |
| `user_id` | String | Yes | |
| `created_at` | String (ISO 8601) | Yes | |
| `updated_at` | String (ISO 8601) | Yes | |

### Document

Represents an ingested file. Created by the document ingest pipeline.

| Property | Type | Required | Notes |
|---|---|---|---|
| `lumogis_id` | String | Yes | `file_path` (unique identifier) |
| `file_path` | String | Yes | |
| `file_type` | String | Yes | Extension: pdf, txt, docx, etc. |
| `user_id` | String | Yes | |
| `ingested_at` | String (ISO 8601) | Yes | |

### Project

Represents a named project, initiative, or effort.

| Property | Type | Required | Notes |
|---|---|---|---|
| `lumogis_id` | String (UUID) | Yes | FK to `entities.entity_id` |
| `name` | String | Yes | |
| `aliases` | String[] | No | |
| `context_tags` | String[] | No | |
| `mention_count` | Integer | Yes | |
| `user_id` | String | Yes | |
| `created_at` | String (ISO 8601) | Yes | |
| `updated_at` | String (ISO 8601) | Yes | |

### Concept

Represents an abstract idea, domain term, or recurring theme.

| Property | Type | Required | Notes |
|---|---|---|---|
| `lumogis_id` | String (UUID) | Yes | FK to `entities.entity_id` |
| `name` | String | Yes | |
| `aliases` | String[] | No | |
| `context_tags` | String[] | No | |
| `mention_count` | Integer | Yes | |
| `user_id` | String | Yes | |
| `created_at` | String (ISO 8601) | Yes | |
| `updated_at` | String (ISO 8601) | Yes | |

---

## Edge Types

All edges carry a `timestamp` (ISO 8601 string) and a `user_id` scoping the provenance.

#### MENTIONS

Source: `Document` or `Session` node → Target: `Person | Organisation | Project | Concept`

Meaning: this document or session references the target entity.

| Property | Type | Notes |
|---|---|---|
| `evidence_id` | String | Session UUID or file_path |
| `evidence_type` | String | `SESSION` or `DOCUMENT` |
| `timestamp` | String | When the mention was recorded |
| `user_id` | String | |

#### RELATES_TO

Source: Any entity → Target: Any entity

Meaning: two entities are semantically related (co-occur with high frequency or share context_tags).

| Property | Type | Notes |
|---|---|---|
| `strength` | Float | Co-occurrence score 0.0–1.0 |
| `timestamp` | String | |
| `user_id` | String | |

#### WORKED_ON

Source: `Person` → Target: `Project`

Meaning: a person was involved in a project, inferred from session or document mentions.

| Property | Type | Notes |
|---|---|---|
| `evidence_id` | String | |
| `timestamp` | String | |
| `user_id` | String | |

---

## Cypher Examples (FalkorDB)

```cypher
-- Create a Person node
CREATE (:Person {lumogis_id: "uuid-here", name: "Ada Lovelace", mention_count: 3, user_id: "default", created_at: "2026-03-17T12:00:00Z", updated_at: "2026-03-17T12:00:00Z"})

-- Create a MENTIONS edge from a Document to a Person
MATCH (d:Document {lumogis_id: "/data/notes.pdf"}), (p:Person {lumogis_id: "uuid-here"})
CREATE (d)-[:MENTIONS {evidence_type: "DOCUMENT", evidence_id: "/data/notes.pdf", timestamp: "2026-03-17T12:00:00Z", user_id: "default"}]->(p)

-- Find all entities mentioned in a document
MATCH (d:Document {lumogis_id: "/data/notes.pdf"})-[:MENTIONS]->(e)
RETURN e.name, labels(e), e.mention_count

-- Find all documents mentioning a person
MATCH (d:Document)-[:MENTIONS]->(p:Person {name: "Ada Lovelace"})
RETURN d.file_path, d.ingested_at
```

---

## Implementation Notes

- **Language preservation**: entity names are stored in the original language of the source material. A German document mentioning "Bundesamt für Statistik" creates a node named "Bundesamt für Statistik", not "Federal Statistical Office".
- **`lumogis_id` links Postgres ↔ graph**: for entities, use `entities.entity_id`; for documents, use the `file_path`.
- **Graph writes are plugin responsibility**: lumogis fires `Event.ENTITY_CREATED` and `Event.DOCUMENT_INGESTED` hooks. A graph plugin subscribes to these and writes nodes/edges. Core never imports a graph adapter.
- **FalkorDB graph name**: always `lumogis`. Multi-tenancy is handled via the `user_id` property, not separate graph instances.
