"""Entity extraction, resolution, and storage.

Extracts structured entities from session/document text using the local LLM,
resolves them against existing entities using name + context_tag overlap,
persists to Postgres (entities + entity_relations + review_queue tables) and
Qdrant (entities collection), and fires Event.ENTITY_CREATED hooks.

Resolution rules (Chunk 9):
  overlap >= 2 context_tags  → merge (append aliases, increment mention_count)
  overlap == 1 context_tag   → ambiguous (log to review_queue, create separate)
  overlap == 0 context_tags  → create separate entity

Entity names are preserved in the original language of the source text.
"""

import json
import logging
import uuid

import config
import hooks
from events import Event
from models.entities import ExtractedEntity

_log = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
Extract all named entities from the text below. For each entity, identify:
- name: the exact name as it appears in the text (do NOT translate — preserve the original language)
- entity_type: one of PERSON, ORG, PROJECT, CONCEPT
- aliases: alternative names for this entity found in the text (empty list if none)
- context_tags: 2-5 short lowercase tags describing the domain or topic (e.g. ["finance", "engineering"])

Return a JSON array of objects. Each object must have exactly these keys: name, entity_type, aliases, context_tags.
If no entities are found, return an empty array [].

Example output:
[
  {"name": "Ada Lovelace", "entity_type": "PERSON", "aliases": ["Ada"], "context_tags": ["computing", "mathematics"]},
  {"name": "Bundesamt für Statistik", "entity_type": "ORG", "aliases": ["BFS"], "context_tags": ["government", "statistics"]}
]

Text to analyse:
"""


def extract_entities(session_text: str) -> list[ExtractedEntity]:
    """Call the local model to extract named entities from session text.

    Returns a list of ExtractedEntity objects. Returns an empty list on
    any LLM or parse failure (never raises — caller continues without entities).
    """
    if not session_text or not session_text.strip():
        return []

    provider = config.get_llm_provider("llama")
    try:
        response = provider.chat(
            messages=[{"role": "user", "content": _EXTRACT_PROMPT + session_text}],
            system=(
                "You are a precise entity extractor. "
                "Respond only with a valid JSON array. "
                "Never translate entity names — preserve the original language."
            ),
            max_tokens=1024,
        )
        raw = response.text.strip()

        # Strip markdown code fences if the model wrapped the JSON
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                line for line in lines if not line.startswith("```")
            )

        data = json.loads(raw)
        if not isinstance(data, list):
            _log.warning("Entity extraction returned non-list JSON, skipping")
            return []

        entities = []
        for item in data:
            try:
                entities.append(
                    ExtractedEntity(
                        name=item["name"],
                        entity_type=item.get("entity_type", "CONCEPT"),
                        aliases=item.get("aliases", []),
                        context_tags=item.get("context_tags", []),
                    )
                )
            except (KeyError, TypeError) as exc:
                _log.debug("Skipping malformed entity item %r: %s", item, exc)

        _log.info("Extracted %d entities from session text", len(entities))
        return entities

    except json.JSONDecodeError as exc:
        _log.warning("Entity extraction: JSON parse failed: %s", exc)
        return []
    except Exception as exc:
        _log.warning("Entity extraction failed: %s", exc)
        return []


def resolve_entity(entity: ExtractedEntity, existing: dict | None) -> str:
    """Determine how to handle an incoming entity against an existing DB candidate.

    Args:
        entity:   Newly extracted entity.
        existing: DB row dict with at least a ``context_tags`` list, or None.

    Returns:
        ``'merge'``     — overlap >= 2 context_tags → merge into existing.
        ``'new'``       — no existing candidate, or overlap == 0 → insert new entity.
        ``'ambiguous'`` — overlap == 1 → log to review_queue, insert as separate entity.
    """
    if existing is None:
        return "new"

    existing_tags = set(existing.get("context_tags") or [])
    incoming_tags = set(entity.context_tags or [])
    overlap = len(existing_tags & incoming_tags)

    if overlap >= 2:
        return "merge"
    if overlap == 0:
        return "new"
    return "ambiguous"  # overlap == 1


def store_entities(
    entities: list[ExtractedEntity],
    evidence_id: str,
    evidence_type: str,
    user_id: str = "default",
) -> None:
    """Persist extracted entities to Postgres + Qdrant and fire hooks.

    For each entity, resolve_entity() decides whether to merge, create new,
    or log as ambiguous. Ambiguous matches are written to review_queue AND
    stored as a separate entity. MENTIONED_IN_SESSION / MENTIONED_IN_DOCUMENT
    relations are always recorded regardless of resolution outcome.
    """
    if not entities:
        return

    ms = config.get_metadata_store()
    embedder = config.get_embedder()
    vs = config.get_vector_store()

    for entity in entities:
        try:
            _upsert_entity(entity, evidence_id, evidence_type, user_id, ms, embedder, vs)
        except Exception as exc:
            _log.error(
                "Failed to store entity %r (%s): %s",
                entity.name,
                entity.entity_type,
                exc,
            )


def _insert_new_entity(entity: ExtractedEntity, user_id: str, ms) -> str:
    """Insert a brand-new entity row and return its UUID."""
    entity_id = str(uuid.uuid4())
    aliases_clean = [a for a in entity.aliases if a.lower() != entity.name.lower()]
    ms.execute(
        "INSERT INTO entities "
        "(entity_id, name, entity_type, aliases, context_tags, mention_count, user_id) "
        "VALUES (%s, %s, %s, %s, %s, 1, %s)",
        (
            entity_id,
            entity.name,
            entity.entity_type,
            aliases_clean,
            entity.context_tags,
            user_id,
        ),
    )
    _log.debug("Inserted entity %r (id=%s, type=%s)", entity.name, entity_id, entity.entity_type)
    return entity_id


def _upsert_entity(
    entity: ExtractedEntity,
    evidence_id: str,
    evidence_type: str,
    user_id: str,
    ms,
    embedder,
    vs,
) -> None:
    """Resolve, store, relate, embed and hook a single entity."""
    # Search by exact name OR as a known alias (case-insensitive)
    existing = ms.fetch_one(
        "SELECT entity_id, name, aliases, context_tags, mention_count "
        "FROM entities "
        "WHERE user_id = %s AND ("
        "  lower(name) = lower(%s) "
        "  OR lower(%s) = ANY(SELECT lower(a) FROM unnest(aliases) a)"
        ")",
        (user_id, entity.name, entity.name),
    )

    decision = resolve_entity(entity, existing)

    if decision == "merge" and existing:
        entity_id = existing["entity_id"]
        merged_aliases = list(
            {*existing["aliases"], *entity.aliases} - {entity.name.lower()}
        )
        # Re-add any alias that is NOT the canonical name (case-insensitive check)
        merged_aliases = [
            a for a in merged_aliases if a.lower() != existing["name"].lower()
        ]
        merged_tags = list({*existing["context_tags"], *entity.context_tags})
        ms.execute(
            "UPDATE entities "
            "SET mention_count = mention_count + 1, "
            "    aliases = %s, "
            "    context_tags = %s, "
            "    updated_at = NOW() "
            "WHERE entity_id = %s",
            (merged_aliases, merged_tags, entity_id),
        )
        _log.debug(
            "Merged entity %r into existing %r (id=%s, +1 mention)",
            entity.name,
            existing["name"],
            entity_id,
        )

    elif decision == "ambiguous" and existing:
        _log.info(
            "Ambiguous entity match: %r vs existing %r (1 tag overlap) — logging to review_queue",
            entity.name,
            existing["name"],
        )
        entity_id = _insert_new_entity(entity, user_id, ms)
        reason = (
            f"Name match with only 1 context_tag overlap: "
            f"'{existing['name']}' vs '{entity.name}'"
        )
        ms.execute(
            "INSERT INTO review_queue "
            "(candidate_a_id, candidate_b_id, reason, user_id) "
            "VALUES (%s, %s, %s, %s)",
            (existing["entity_id"], entity_id, reason, user_id),
        )

    else:
        # decision == "new" — no match or zero tag overlap → fresh entity
        entity_id = _insert_new_entity(entity, user_id, ms)

    relation_type = (
        "MENTIONED_IN_SESSION" if evidence_type == "SESSION" else "MENTIONED_IN_DOCUMENT"
    )
    ms.execute(
        "INSERT INTO entity_relations "
        "(source_id, relation_type, evidence_type, evidence_id, user_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (entity_id, relation_type, evidence_type, evidence_id, user_id),
    )

    embed_text = f"{entity.name} ({entity.entity_type})"
    if entity.context_tags:
        embed_text += f": {', '.join(entity.context_tags)}"
    vector = embedder.embed(embed_text)

    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"entity::{user_id}::{entity.name.lower()}"))
    vs.upsert(
        collection="entities",
        id=point_id,
        vector=vector,
        payload={
            "entity_id": entity_id,
            "name": entity.name,
            "entity_type": entity.entity_type,
            "aliases": entity.aliases,
            "context_tags": entity.context_tags,
            "user_id": user_id,
        },
    )

    hooks.fire(
        Event.ENTITY_CREATED,
        entity_id=entity_id,
        name=entity.name,
        entity_type=entity.entity_type,
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        user_id=user_id,
    )
