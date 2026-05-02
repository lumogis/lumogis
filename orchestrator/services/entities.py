# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Entity extraction, resolution, and storage.

Extracts structured entities from session/document text using the local LLM,
resolves them against existing entities using name + context_tag overlap,
persists to Postgres (entities + entity_relations + review_queue tables) and
Qdrant (entities collection), and fires Event.ENTITY_CREATED hooks.

Resolution rules:
  overlap >= 2 context_tags  → merge (append aliases, increment mention_count)
  overlap == 1 context_tag   → ambiguous (log to review_queue, create separate)
  overlap == 0 context_tags  → create separate entity

Entity names are preserved in the original language of the source text.
"""

import json
import logging
import os
import uuid
from typing import Optional

import hooks
from auth import UserContext
from events import Event
from models.entities import ExtractedEntity
from visibility import visible_filter

import config
from services import entity_constraints
from services import entity_quality

_log = logging.getLogger(__name__)


def resolve_relation_source_id(entity_row: dict) -> str:
    """Return the personal source `entity_id` for an entity row.

    For shared/system projection rows, ``entity_relations`` always live on
    the original personal source — readers must follow ``published_from``
    back to that source before querying relations. For personal rows
    (no projection), ``published_from`` is ``NULL`` and the row's own
    ``entity_id`` is the answer. See plan §2.4 rule 9 + the
    cross-user-invisibility scenario in §2.15.
    """
    pf = entity_row.get("published_from")
    return pf if pf else entity_row["entity_id"]


_EXTRACT_PROMPT = """\
Extract all named entities from the text below. For each entity, identify:
- name: the exact name as it appears in the text (do NOT translate — preserve the original language)
- entity_type: one of PERSON, ORG, PROJECT, CONCEPT
- aliases: alternative names for this entity found in the text (empty list if none)
- context_tags: 2-5 short lowercase tags for domain/topic (e.g. ["finance", "engineering"])

Return a JSON array of objects with keys: name, entity_type, aliases, context_tags.
If no entities are found, return an empty array [].

Example output:
[
  {"name": "Ada Lovelace", "entity_type": "PERSON",
   "aliases": ["Ada"], "context_tags": ["computing", "mathematics"]},
  {"name": "Bundesamt für Statistik", "entity_type": "ORG",
   "aliases": ["BFS"], "context_tags": ["government", "statistics"]}
]

Text to analyse:
"""


def extract_entities(session_text: str, *, user_id: str | None = None) -> list[ExtractedEntity]:
    """Call the local model to extract named entities from session text.

    Returns a list of ExtractedEntity objects. Returns an empty list on
    any LLM or parse failure (never raises — caller continues without entities).

    Plan llm_provider_keys_per_user_migration Pass 2.10: ``user_id`` is
    threaded into ``get_llm_provider`` so a future switch to a cloud model
    resolves the key per-user. ``llama`` has no ``api_key_env`` so user_id
    is a no-op semantically today.
    """
    if not session_text or not session_text.strip():
        return []

    from services.connector_credentials import ConnectorNotConfigured
    from services.connector_credentials import CredentialUnavailable

    try:
        provider = config.get_llm_provider("llama", user_id=user_id)
    except ConnectorNotConfigured as exc:
        _log.warning(
            "extract_entities: missing per-user credential (user=%s): %s",
            user_id,
            exc,
        )
        return []
    except CredentialUnavailable as exc:
        _log.warning(
            "extract_entities: stored credential unusable (user=%s): %s",
            user_id,
            exc,
        )
        return []
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
            raw = "\n".join(line for line in lines if not line.startswith("```"))

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
) -> list[str]:
    """Persist extracted entities to Postgres + Qdrant and fire hooks.

    For each entity, resolve_entity() decides whether to merge, create new,
    or log as ambiguous. Ambiguous matches are written to review_queue AND
    stored as a separate entity. MENTIONED_IN_SESSION / MENTIONED_IN_DOCUMENT
    relations are always recorded regardless of resolution outcome.

    Returns the list of resolved entity_id UUIDs (one per entity, in order).
    Failed entities are skipped and excluded from the returned list.
    """
    if not entities:
        return []

    entities, discarded_count = entity_quality.score_and_filter_entities(entities, user_id)
    if discarded_count > 0:
        _log.debug(
            "Entity quality gate discarded %d low-quality entities for user=%s",
            discarded_count,
            user_id,
        )

    if not entities:
        return []

    ms = config.get_metadata_store()
    embedder = config.get_embedder()
    vs = config.get_vector_store()

    entity_ids: list[str] = []
    for entity in entities:
        try:
            entity_id = _upsert_entity(
                entity, evidence_id, evidence_type, user_id, ms, embedder, vs
            )
            entity_ids.append(entity_id)
        except Exception as exc:
            _log.error(
                "Failed to store entity %r (%s): %s",
                entity.name,
                entity.entity_type,
                exc,
            )

    if entity_ids:
        new_violations = entity_constraints.run_batch_constraints(entity_ids, user_id)
        if new_violations > 0:
            _log.debug(
                "Constraint validation: %d new violation(s) for user=%s",
                new_violations,
                user_id,
            )

    return entity_ids


def _insert_new_entity(entity: ExtractedEntity, user_id: str, ms) -> str:
    """Insert a brand-new entity row and return its UUID.

    Always writes ``scope='personal'``. The publish path
    (``services/projection.py``) creates the shared/system projection rows
    with ``published_from`` set; the personal source is never mutated.
    """
    entity_id = str(uuid.uuid4())
    aliases_clean = [a for a in entity.aliases if a.lower() != entity.name.lower()]
    is_staged = bool(entity.is_staged) if entity.is_staged is not None else False
    ms.execute(
        "INSERT INTO entities "
        "(entity_id, name, entity_type, aliases, context_tags, mention_count, user_id, "
        " extraction_quality, is_staged, scope) "
        "VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, 'personal')",
        (
            entity_id,
            entity.name,
            entity.entity_type,
            aliases_clean,
            entity.context_tags,
            user_id,
            entity.extraction_quality,
            is_staged,
        ),
    )
    _log.debug(
        "Inserted entity %r (id=%s, type=%s, quality=%.3f, staged=%s)",
        entity.name,
        entity_id,
        entity.entity_type,
        entity.extraction_quality or 0.0,
        is_staged,
    )
    return entity_id


def _upsert_entity(
    entity: ExtractedEntity,
    evidence_id: str,
    evidence_type: str,
    user_id: str,
    ms,
    embedder,
    vs,
) -> str:
    """Resolve, store, relate, embed and hook a single entity. Returns entity_id."""
    # SCOPE-EXEMPT: personal-scope precheck for write-side merge target.
    # Merging targets the user's own personal source of truth; we deliberately
    # do NOT consider other users' personal rows or any shared/system
    # projection rows when looking for an existing match, because (a)
    # cross-user merge would leak attribution and (b) projection rows have
    # `published_from != NULL` and are derived data — never write targets.
    # The query is narrowed with `AND scope = 'personal'` per plan §2.11.
    existing = ms.fetch_one(
        "SELECT entity_id, name, aliases, context_tags, mention_count, is_staged "
        "FROM entities "
        # SCOPE-EXEMPT: write-side merge target lookup (see comment above).
        "WHERE user_id = %s AND scope = 'personal' AND published_from IS NULL "
        "  AND ("
        "  lower(name) = lower(%s) "
        "  OR lower(%s) = ANY(SELECT lower(a) FROM unnest(aliases) a)"
        ")",
        (user_id, entity.name, entity.name),
    )

    decision = resolve_entity(entity, existing)

    if decision == "merge" and existing:
        entity_id = existing["entity_id"]
        merged_aliases = list({*existing["aliases"], *entity.aliases} - {entity.name.lower()})
        # Re-add any alias that is NOT the canonical name (case-insensitive check)
        merged_aliases = [a for a in merged_aliases if a.lower() != existing["name"].lower()]
        merged_tags = list({*existing["context_tags"], *entity.context_tags})

        promote_on_count = int(os.environ.get("ENTITY_PROMOTE_ON_MENTION_COUNT", "3"))
        upper_threshold = float(os.environ.get("ENTITY_QUALITY_UPPER", "0.60"))
        existing_is_staged = existing.get("is_staged", False)
        new_mention_count = existing["mention_count"] + 1

        should_promote = existing_is_staged and (
            (entity.extraction_quality is not None and entity.extraction_quality > upper_threshold)
            or new_mention_count >= promote_on_count
        )

        if should_promote:
            ms.execute(
                "UPDATE entities "
                "SET mention_count = mention_count + 1, "
                "    aliases = %s, "
                "    context_tags = %s, "
                "    is_staged = FALSE, "
                "    graph_projected_at = NULL, "
                "    updated_at = NOW() "
                "WHERE entity_id = %s",
                (merged_aliases, merged_tags, entity_id),
            )
            _log.debug(
                "Merged entity %r into existing %r (id=%s, +1 mention) — promoted from staged",
                entity.name,
                existing["name"],
                entity_id,
            )
        else:
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
            f"Name match with only 1 context_tag overlap: '{existing['name']}' vs '{entity.name}'"
        )
        ms.execute(
            "INSERT INTO review_queue "
            "(candidate_a_id, candidate_b_id, reason, user_id, scope) "
            "VALUES (%s, %s, %s, %s, 'personal')",
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
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (source_id, evidence_id, relation_type, user_id) DO NOTHING",
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
            "scope": "personal",
        },
    )

    hooks.fire_background(
        Event.ENTITY_CREATED,
        entity_id=entity_id,
        name=entity.name,
        entity_type=entity.entity_type,
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        user_id=user_id,
        is_staged=bool(entity.is_staged) if entity.is_staged is not None else False,
    )
    return entity_id


# ----------------------------------------------------------------------
# Read helpers (Area 4 — backing the MCP entity.lookup / entity.search
# tools). Thin Postgres wrappers, no business logic. They mirror the
# error-handling pattern of routes/data.py::list_entities: on any DB
# failure, log a WARNING and return the empty answer (None / []), never
# raise. Callers (MCP tools, future REST surfaces) treat empty as
# "nothing found", which is the only safe answer when the DB is down.
# ----------------------------------------------------------------------


def lookup_by_name(
    name: str,
    user_id: str = "default",
    *,
    scope_filter: Optional[str] = None,
) -> dict | None:
    """Find an entity by exact case-insensitive name match.

    Returns a serialisable dict or None when no match (or DB failure).
    Aliases are NOT searched — this is the strict-lookup path used by
    `entity.lookup`. For partial matches use `search_by_name`.

    Visibility: applies :func:`visibility.visible_filter` so the result
    can be a personal-mine, shared, or system entity. ``scope`` is
    included in the result dict so callers can render badges. The MCP
    `entity.lookup` schema requires this field (plan §6).
    """
    if not name or not name.strip():
        return None

    ms = config.get_metadata_store()
    user = UserContext(user_id=user_id)
    where_clause, where_params = visible_filter(user, scope_filter)
    try:
        row = ms.fetch_one(
            "SELECT name, entity_type, mention_count, aliases, context_tags, scope "
            "FROM entities "
            f"WHERE {where_clause} AND lower(name) = lower(%s) "
            "LIMIT 1",
            (*where_params, name.strip()),
        )
    except Exception as exc:
        _log.warning("lookup_by_name: DB query failed — %s", exc)
        return None

    if row is None:
        return None
    return {
        "name": row["name"],
        "entity_type": row["entity_type"],
        "mention_count": row["mention_count"],
        "aliases": row.get("aliases") or [],
        "context_tags": row.get("context_tags") or [],
        "scope": row.get("scope", "personal"),
    }


def search_by_name(
    query: str,
    limit: int = 10,
    user_id: str = "default",
    *,
    scope_filter: Optional[str] = None,
) -> list[dict]:
    """Find entities whose name matches `query` as a case-insensitive substring.

    Ordered by mention_count descending so the strongest signals surface
    first. Returns [] on empty/whitespace query or any DB failure.

    Visibility: applies :func:`visibility.visible_filter` (household union
    by default; ``scope_filter`` narrows). Each result row includes
    ``scope`` per plan §6.
    """
    if not query or not query.strip():
        return []

    ms = config.get_metadata_store()
    user = UserContext(user_id=user_id)
    pattern = f"%{query.strip()}%"
    where_clause, where_params = visible_filter(user, scope_filter)
    try:
        rows = ms.fetch_all(
            "SELECT name, entity_type, mention_count, aliases, context_tags, scope "
            "FROM entities "
            f"WHERE {where_clause} AND name ILIKE %s "
            "ORDER BY mention_count DESC "
            "LIMIT %s",
            (*where_params, pattern, limit),
        )
    except Exception as exc:
        _log.warning("search_by_name: DB query failed — %s", exc)
        return []

    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "mention_count": r["mention_count"],
            "aliases": r.get("aliases") or [],
            "context_tags": r.get("context_tags") or [],
            "scope": r.get("scope", "personal"),
        }
        for r in rows
    ]
