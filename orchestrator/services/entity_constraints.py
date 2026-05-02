# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Constraint validation layer — Pass 2 of the KG Quality Pipeline.

Runs data quality rules against ingested entities and writes violations to the
constraint_violations table.  Never raises — all exceptions are caught and
logged so ingestion is never blocked.

Per-ingest rules (run by run_batch_constraints on every store_entities() call):
  person_name_required        CRITICAL  Person with empty/null name
  organisation_name_required  CRITICAL  Org with empty/null name
  no_self_loop                CRITICAL  entity_relations row where the entity's own UUID
                                        appears as evidence_id (entity cites itself as its
                                        own evidence source — a data wiring error).
                                        NOTE: entity_relations is a provenance table with no
                                        target-entity column, so a graph-layer self-loop
                                        (source == target) cannot be expressed here.  This
                                        rule catches the Postgres-level equivalent.
  valid_edge_type             CRITICAL  Relation type not in the allowed set.
                                        Allowed set covers both provenance edges written by
                                        store_entities() and semantic edges added by future
                                        passes.
  person_completeness         INFO      Person entity with zero MENTIONS edges

Corpus-level rules (called separately by the weekly job, deferred to Pass 3):
  orphan_entity          WARNING   Entity with zero edges, created > 7 days ago
  alias_uniqueness       WARNING   Two distinct entities sharing the same alias

Auto-resolution: when a constraint is re-checked and the condition no longer
holds, any open violation for that entity + rule is resolved by setting
resolved_at = NOW().
"""

import logging

import config

_log = logging.getLogger(__name__)

_ALLOWED_EDGE_TYPES = frozenset({
    # Provenance edges — inserted by store_entities() on every ingest
    "MENTIONED_IN_SESSION",
    "MENTIONED_IN_DOCUMENT",
    "RELATED_TO",
    # Semantic edges — used by future passes and graph projection
    "MENTIONS",
    "RELATES_TO",
    "DISCUSSED_IN",
    "DERIVED_FROM",
    "WORKED_ON",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _insert_violation(ms, user_id: str, entity_id: str, rule_name: str, severity: str, detail: str) -> None:
    ms.execute(
        "INSERT INTO constraint_violations (user_id, entity_id, rule_name, severity, detail) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, entity_id, rule_name, severity, detail),
    )


def _resolve_violation(ms, entity_id: str, rule_name: str) -> None:
    """Mark any open violation for (entity_id, rule_name) as resolved."""
    ms.execute(
        "UPDATE constraint_violations "
        "SET resolved_at = NOW() "
        "WHERE entity_id = %s AND rule_name = %s AND resolved_at IS NULL",
        (entity_id, rule_name),
    )


def _open_violation_exists(ms, entity_id: str, rule_name: str) -> bool:
    row = ms.fetch_one(
        "SELECT 1 FROM constraint_violations "
        "WHERE entity_id = %s AND rule_name = %s AND resolved_at IS NULL "
        "LIMIT 1",
        (entity_id, rule_name),
    )
    return row is not None


# ---------------------------------------------------------------------------
# Per-entity rule checkers
# Each returns True if a violation was found, False if the entity is compliant.
# Responsible for both inserting new violations and auto-resolving stale ones.
# ---------------------------------------------------------------------------

def _check_person_name_required(ms, entity_id: str, user_id: str) -> bool:
    rule = "person_name_required"
    try:
        row = ms.fetch_one(
            "SELECT name FROM entities WHERE entity_id = %s AND entity_type = 'PERSON'",
            (entity_id,),
        )
        if row is None:
            # Not a person — resolve any stale violation and return clean
            _resolve_violation(ms, entity_id, rule)
            return False

        name = (row.get("name") or "").strip()
        if not name:
            if not _open_violation_exists(ms, entity_id, rule):
                _insert_violation(ms, user_id, entity_id, rule, "CRITICAL",
                                   "Person entity has empty or null name")
                _log.critical("constraint_violation rule=%s entity_id=%s user_id=%s", rule, entity_id, user_id)
            return True
        else:
            _resolve_violation(ms, entity_id, rule)
            return False
    except Exception:
        _log.exception("constraint: rule=%s entity_id=%s failed", rule, entity_id)
        return False


def _check_organisation_name_required(ms, entity_id: str, user_id: str) -> bool:
    rule = "organisation_name_required"
    try:
        row = ms.fetch_one(
            "SELECT name FROM entities WHERE entity_id = %s AND entity_type = 'ORG'",
            (entity_id,),
        )
        if row is None:
            _resolve_violation(ms, entity_id, rule)
            return False

        name = (row.get("name") or "").strip()
        if not name:
            if not _open_violation_exists(ms, entity_id, rule):
                _insert_violation(ms, user_id, entity_id, rule, "CRITICAL",
                                   "Organisation entity has empty or null name")
                _log.critical("constraint_violation rule=%s entity_id=%s user_id=%s", rule, entity_id, user_id)
            return True
        else:
            _resolve_violation(ms, entity_id, rule)
            return False
    except Exception:
        _log.exception("constraint: rule=%s entity_id=%s failed", rule, entity_id)
        return False


def _check_no_self_loop(ms, entity_id: str, user_id: str) -> bool:
    """Detect entity_relations rows where an entity cites itself as its own evidence.

    entity_relations is a provenance table: source_id is the entity UUID and
    evidence_id is a TEXT field holding a session UUID or file path.  There is
    no separate target-entity column, so a graph-layer self-loop (source == target
    entity) cannot be expressed in this table.

    What this rule catches: a row where source_id::text == evidence_id, meaning
    the entity's own UUID was accidentally stored as the evidence identifier.
    This indicates a data wiring error — the caller passed the entity UUID where
    it should have passed a session or document ID.

    Severity: CRITICAL — the evidence provenance for that row is corrupt.
    """
    rule = "no_self_loop"
    try:
        rows = ms.fetch_all(
            "SELECT id FROM entity_relations "
            "WHERE source_id = %s AND evidence_id = %s",
            (entity_id, entity_id),
        )
        if rows:
            if not _open_violation_exists(ms, entity_id, rule):
                _insert_violation(ms, user_id, entity_id, rule, "CRITICAL",
                                   f"Self-loop detected: source_id == evidence_id == {entity_id}")
                _log.critical("constraint_violation rule=%s entity_id=%s user_id=%s", rule, entity_id, user_id)
            return True
        else:
            _resolve_violation(ms, entity_id, rule)
            return False
    except Exception:
        _log.exception("constraint: rule=%s entity_id=%s failed", rule, entity_id)
        return False


def _check_valid_edge_type(ms, entity_id: str, user_id: str) -> bool:
    rule = "valid_edge_type"
    try:
        rows = ms.fetch_all(
            "SELECT DISTINCT relation_type FROM entity_relations WHERE source_id = %s",
            (entity_id,),
        )
        invalid_types = [
            r["relation_type"] for r in rows
            if r.get("relation_type") not in _ALLOWED_EDGE_TYPES
        ]
        if invalid_types:
            detail = f"Invalid edge type(s): {', '.join(sorted(invalid_types))}"
            if not _open_violation_exists(ms, entity_id, rule):
                _insert_violation(ms, user_id, entity_id, rule, "CRITICAL", detail)
                _log.critical("constraint_violation rule=%s entity_id=%s user_id=%s detail=%r",
                               rule, entity_id, user_id, detail)
            return True
        else:
            _resolve_violation(ms, entity_id, rule)
            return False
    except Exception:
        _log.exception("constraint: rule=%s entity_id=%s failed", rule, entity_id)
        return False


def _check_person_completeness(ms, entity_id: str, user_id: str) -> bool:
    """Flag Person entities with zero MENTIONS edges (INFO severity).

    A Person that has been mentioned across sessions/documents but has no
    explicit MENTIONS relation is missing completeness signal.  This is a
    soft quality hint — it does not block ingest.
    """
    rule = "person_completeness"
    try:
        row = ms.fetch_one(
            "SELECT 1 FROM entities WHERE entity_id = %s AND entity_type = 'PERSON'",
            (entity_id,),
        )
        if row is None:
            # Not a person — resolve any stale violation and skip
            _resolve_violation(ms, entity_id, rule)
            return False

        mention_row = ms.fetch_one(
            "SELECT 1 FROM entity_relations "
            "WHERE source_id = %s AND relation_type = 'MENTIONS' "
            "LIMIT 1",
            (entity_id,),
        )
        if mention_row is None:
            if not _open_violation_exists(ms, entity_id, rule):
                _insert_violation(ms, user_id, entity_id, rule, "INFO",
                                   "Person entity has zero MENTIONS edges")
            return True
        else:
            _resolve_violation(ms, entity_id, rule)
            return False
    except Exception:
        _log.exception("constraint: rule=%s entity_id=%s failed", rule, entity_id)
        return False


# ---------------------------------------------------------------------------
# Corpus-level rule checkers (not called on every ingest)
# Called by the weekly job introduced in Pass 3.
# ---------------------------------------------------------------------------

def check_orphan_entities(user_id: str) -> int:
    """Corpus-level check: entity with zero edges AND created_at > 7 days ago.

    Returns count of new violations inserted.
    Auto-resolves violations for entities that now have edges.
    Never raises.
    """
    rule = "orphan_entity"
    ms = config.get_metadata_store()
    inserted = 0
    try:
        # SCOPE-EXEMPT: constraint checks are personal-scope-only quality
        # operations on the user's own entities (mirrors plan §2.11 dedup
        # rule); the query is narrowed with `AND scope = 'personal'` so
        # shared/system projections never trigger orphan violations.
        orphans = ms.fetch_all(
            "SELECT entity_id FROM entities "
            "WHERE user_id = %s AND scope = 'personal' "
            "  AND is_staged = FALSE "
            "  AND created_at < NOW() - INTERVAL '7 days' "
            "  AND entity_id NOT IN ("
            "    SELECT DISTINCT source_id FROM entity_relations"
            "  )",
            (user_id,),
        )
        orphan_ids = {str(r["entity_id"]) for r in orphans}

        for entity_id in orphan_ids:
            if not _open_violation_exists(ms, entity_id, rule):
                _insert_violation(ms, user_id, entity_id, rule, "WARNING",
                                   "Entity has zero edges and was created more than 7 days ago")
                inserted += 1

        # SCOPE-EXEMPT: `constraint_violations` is in plan §2.10's
        # excluded-from-scope list (no `scope` column); per-user filtering
        # is the correct visibility model for this table.
        open_violations = ms.fetch_all(
            "SELECT DISTINCT entity_id FROM constraint_violations "
            "WHERE user_id = %s AND rule_name = %s AND resolved_at IS NULL",
            (user_id, rule),
        )
        for row in open_violations:
            eid = str(row["entity_id"])
            if eid not in orphan_ids:
                _resolve_violation(ms, eid, rule)

    except Exception:
        _log.exception("constraint: corpus-level rule=%s user_id=%s failed", rule, user_id)
    return inserted


def check_alias_uniqueness(user_id: str) -> int:
    """Corpus-level check: two distinct entities sharing the same alias value.

    Returns count of new violations inserted (one per involved entity per duplicate alias).
    Never raises.
    """
    rule = "alias_uniqueness"
    ms = config.get_metadata_store()
    inserted = 0
    try:
        # SCOPE-EXEMPT: alias-uniqueness is a personal-scope-only quality
        # check (per plan §2.11 dedup rule); narrowed to `scope='personal'`
        # so shared/system projections never trigger duplicate-alias
        # violations.
        dupe_rows = ms.fetch_all(
            "SELECT alias, array_agg(entity_id) AS entity_ids "
            "FROM ("
            "  SELECT entity_id, lower(unnest(aliases)) AS alias "
            # SCOPE-EXEMPT: personal-only alias uniqueness (see comment above).
            "  FROM entities WHERE user_id = %s AND scope = 'personal'"
            ") sub "
            "GROUP BY alias "
            "HAVING count(DISTINCT entity_id) > 1",
            (user_id,),
        )

        violating_entities: set[str] = set()
        for row in dupe_rows:
            alias_val = row.get("alias") or ""
            entity_ids = row.get("entity_ids") or []
            for eid in entity_ids:
                eid_str = str(eid)
                violating_entities.add(eid_str)
                if not _open_violation_exists(ms, eid_str, rule):
                    detail = f"Alias '{alias_val}' is shared with {len(entity_ids) - 1} other entity/entities"
                    _insert_violation(ms, user_id, eid_str, rule, "WARNING", detail)
                    inserted += 1

        # SCOPE-EXEMPT: `constraint_violations` is in plan §2.10's
        # excluded-from-scope list (no `scope` column); per-user filtering
        # is the correct visibility model for this table.
        open_violations = ms.fetch_all(
            "SELECT DISTINCT entity_id FROM constraint_violations "
            "WHERE user_id = %s AND rule_name = %s AND resolved_at IS NULL",
            (user_id, rule),
        )
        for row in open_violations:
            eid = str(row["entity_id"])
            if eid not in violating_entities:
                _resolve_violation(ms, eid, rule)

    except Exception:
        _log.exception("constraint: corpus-level rule=%s user_id=%s failed", rule, user_id)
    return inserted


# ---------------------------------------------------------------------------
# Public entry point — called at the tail of store_entities()
# ---------------------------------------------------------------------------

def run_batch_constraints(entity_ids: list[str], user_id: str) -> int:
    """Run all per-ingest constraint rules for the given entity IDs.

    Insert violations into constraint_violations table.
    Returns count of new violations inserted.
    Never raises — logs ERROR on exception and returns 0.
    """
    if not entity_ids:
        return 0

    try:
        ms = config.get_metadata_store()
        new_violations = 0

        for entity_id in entity_ids:
            for checker in (
                _check_person_name_required,
                _check_organisation_name_required,
                _check_no_self_loop,
                _check_valid_edge_type,
                _check_person_completeness,
            ):
                try:
                    if checker(ms, entity_id, user_id):
                        new_violations += 1
                except Exception:
                    _log.error(
                        "constraint: checker=%s entity_id=%s raised unexpectedly",
                        checker.__name__,
                        entity_id,
                        exc_info=True,
                    )

        return new_violations

    except Exception:
        _log.error(
            "run_batch_constraints: unexpected error for user_id=%s — returning 0",
            user_id,
            exc_info=True,
        )
        return 0
