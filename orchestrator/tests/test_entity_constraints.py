# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for services/entity_constraints.py — Pass 2 KG constraint validation.

Coverage:
  1.  person_name_required fires for Person with empty name
  2.  person_name_required does NOT fire for Person with a valid name
  3.  organisation_name_required fires for Org with empty name
  4.  organisation_name_required does NOT fire for Org with valid name
  5.  no_self_loop fires when entity UUID appears as its own evidence_id
  6.  no_self_loop does NOT fire when evidence_id is a different value (normal case)
  7.  valid_edge_type fires for an unknown edge type
  8.  valid_edge_type does NOT fire for allowed semantic edge types
  9.  valid_edge_type does NOT fire for provenance edge types (MENTIONED_IN_SESSION etc.)
  10. Auto-resolution: open violation is resolved when condition clears
  11. run_batch_constraints never raises — exception inside a rule is caught and logged
  12. Orphan rule only fires for entities older than 7 days, not newer ones
  13. Alias uniqueness flags two entities sharing an alias, not a single entity
  14. person_completeness fires for Person with zero MENTIONS edges
  15. person_completeness does NOT fire for Person that has a MENTIONS edge
  16. person_completeness does NOT fire for non-Person entities
"""

import uuid
from unittest.mock import MagicMock

import pytest

import config as _config
from services import entity_constraints

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _eid() -> str:
    return str(uuid.uuid4())


def _make_ms(fetch_one_map: dict | None = None, fetch_all_map: dict | None = None):
    """Return a mock MetadataStore where fetch_one/fetch_all return configured values.

    fetch_one_map: {substring_in_query: return_value}
    fetch_all_map: {substring_in_query: return_value}
    """
    ms = MagicMock()
    ms.ping.return_value = True
    ms.execute.return_value = None

    def _fetch_one(query, params=None):
        if fetch_one_map:
            for key, val in fetch_one_map.items():
                if key in query:
                    return val
        return None

    def _fetch_all(query, params=None):
        if fetch_all_map:
            for key, val in fetch_all_map.items():
                if key in query:
                    return val
        return []

    ms.fetch_one.side_effect = _fetch_one
    ms.fetch_all.side_effect = _fetch_all
    return ms


# ---------------------------------------------------------------------------
# 1. person_name_required — fires for empty name
# ---------------------------------------------------------------------------


def test_person_name_required_fires_for_empty_name():
    entity_id = _eid()
    user_id = "default"

    ms = _make_ms(
        fetch_one_map={
            "FROM entities": {"name": ""},
            "FROM constraint_violations": None,  # no open violation yet
        }
    )

    result = entity_constraints._check_person_name_required(ms, entity_id, user_id)

    assert result is True
    assert ms.execute.called
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# 2. person_name_required — does NOT fire for valid name
# ---------------------------------------------------------------------------


def test_person_name_required_clean_entity():
    entity_id = _eid()
    ms = _make_ms(fetch_one_map={"FROM entities": {"name": "Alice Smith"}})

    result = entity_constraints._check_person_name_required(ms, entity_id, "default")

    assert result is False
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 0


# ---------------------------------------------------------------------------
# 3. organisation_name_required — fires for empty name
# ---------------------------------------------------------------------------


def test_organisation_name_required_fires_for_empty_name():
    entity_id = _eid()
    ms = _make_ms(
        fetch_one_map={
            "FROM entities": {"name": "   "},
            "FROM constraint_violations": None,
        }
    )

    result = entity_constraints._check_organisation_name_required(ms, entity_id, "default")

    assert result is True
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# 4. organisation_name_required — does NOT fire for valid name
# ---------------------------------------------------------------------------


def test_organisation_name_required_clean():
    entity_id = _eid()
    ms = _make_ms(fetch_one_map={"FROM entities": {"name": "Acme Corp"}})

    result = entity_constraints._check_organisation_name_required(ms, entity_id, "default")

    assert result is False


# ---------------------------------------------------------------------------
# 5. no_self_loop — fires when entity UUID appears as its own evidence_id
#
# entity_relations has no target-entity column — it is a provenance table.
# The rule catches: source_id::text == evidence_id (entity cites itself as
# the document/session that produced it — a data wiring error).
# ---------------------------------------------------------------------------


def test_no_self_loop_fires_when_entity_is_its_own_evidence():
    entity_id = _eid()
    ms = _make_ms(
        fetch_all_map={"FROM entity_relations": [{"id": 1}]},
        fetch_one_map={"FROM constraint_violations": None},
    )

    result = entity_constraints._check_no_self_loop(ms, entity_id, "default")

    assert result is True
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# 6. no_self_loop — does NOT fire for normal provenance rows
# ---------------------------------------------------------------------------


def test_no_self_loop_clean_when_no_self_referencing_rows():
    entity_id = _eid()
    # Empty result: entity does NOT appear as its own evidence_id
    ms = _make_ms(fetch_all_map={"FROM entity_relations": []})

    result = entity_constraints._check_no_self_loop(ms, entity_id, "default")

    assert result is False


# ---------------------------------------------------------------------------
# 7. valid_edge_type — fires for unknown edge type
# ---------------------------------------------------------------------------


def test_valid_edge_type_fires_for_unknown_type():
    entity_id = _eid()
    ms = _make_ms(
        fetch_all_map={"FROM entity_relations": [{"relation_type": "UNKNOWN_EDGE"}]},
        fetch_one_map={"FROM constraint_violations": None},
    )

    result = entity_constraints._check_valid_edge_type(ms, entity_id, "default")

    assert result is True


# ---------------------------------------------------------------------------
# 8. valid_edge_type — does NOT fire for allowed semantic edge types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "edge_type",
    [
        "MENTIONS",
        "RELATES_TO",
        "DISCUSSED_IN",
        "DERIVED_FROM",
        "WORKED_ON",
    ],
)
def test_valid_edge_type_clean_for_semantic_types(edge_type):
    entity_id = _eid()
    ms = _make_ms(fetch_all_map={"FROM entity_relations": [{"relation_type": edge_type}]})

    result = entity_constraints._check_valid_edge_type(ms, entity_id, "default")

    assert result is False


# ---------------------------------------------------------------------------
# 9. valid_edge_type — does NOT fire for provenance edge types
#
# store_entities() inserts MENTIONED_IN_SESSION and MENTIONED_IN_DOCUMENT on
# every ingest.  These must be in the allowed set or every entity would get
# an immediate false-positive CRITICAL violation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "edge_type",
    [
        "MENTIONED_IN_SESSION",
        "MENTIONED_IN_DOCUMENT",
        "RELATED_TO",
    ],
)
def test_valid_edge_type_clean_for_provenance_types(edge_type):
    entity_id = _eid()
    ms = _make_ms(fetch_all_map={"FROM entity_relations": [{"relation_type": edge_type}]})

    result = entity_constraints._check_valid_edge_type(ms, entity_id, "default")

    assert result is False


# ---------------------------------------------------------------------------
# 10. Auto-resolution: violation resolved when condition clears
# ---------------------------------------------------------------------------


def test_auto_resolve_when_condition_clears():
    entity_id = _eid()

    # Entity now has a valid name (condition no longer holds)
    ms = _make_ms(fetch_one_map={"FROM entities": {"name": "Alice Smith"}})

    result = entity_constraints._check_person_name_required(ms, entity_id, "default")

    assert result is False
    # The UPDATE resolved_at = NOW() call should have been issued
    update_calls = [c for c in ms.execute.call_args_list if "resolved_at = NOW()" in c[0][0]]
    assert len(update_calls) == 1


# ---------------------------------------------------------------------------
# 11. run_batch_constraints never raises
# ---------------------------------------------------------------------------


def test_run_batch_constraints_never_raises(monkeypatch):
    """An exception inside any rule checker must not propagate out of run_batch_constraints."""

    def _exploding_checker(ms, entity_id, user_id):
        raise RuntimeError("intentional test explosion")

    monkeypatch.setattr(
        entity_constraints,
        "_check_person_name_required",
        _exploding_checker,
    )

    entity_id = _eid()
    # Should not raise even though the inner checker explodes
    result = entity_constraints.run_batch_constraints([entity_id], "default")

    # Still returns an int (may be 0 or partial count depending on which rules ran)
    assert isinstance(result, int)
    assert result >= 0


# ---------------------------------------------------------------------------
# 12. Orphan rule — fires only for entities older than 7 days
# ---------------------------------------------------------------------------


def test_orphan_entity_only_fires_for_old_entities(monkeypatch):
    entity_id_old = _eid()

    ms = MagicMock()
    ms.ping.return_value = True
    ms.execute.return_value = None

    # fetch_all returns different values based on query content
    def _fetch_all(query, params=None):
        if "7 days" in query and "entity_id NOT IN" in query:
            # The orphan query: return only the old entity
            return [{"entity_id": entity_id_old}]
        if "resolved_at IS NULL" in query:
            # No previously open violations to auto-resolve
            return []
        return []

    ms.fetch_all.side_effect = _fetch_all

    def _fetch_one(query, params=None):
        # No open violation exists yet
        return None

    ms.fetch_one.side_effect = _fetch_one

    monkeypatch.setattr(_config, "_instances", {"metadata_store": ms})

    inserted = entity_constraints.check_orphan_entities("default")

    assert inserted == 1
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 1


def test_orphan_entity_does_not_fire_for_new_entity(monkeypatch):
    ms = MagicMock()
    ms.ping.return_value = True
    ms.execute.return_value = None

    def _fetch_all(query, params=None):
        if "7 days" in query and "entity_id NOT IN" in query:
            # No orphans (new entity created recently, not returned by the >7d query)
            return []
        return []

    ms.fetch_all.side_effect = _fetch_all

    monkeypatch.setattr(_config, "_instances", {"metadata_store": ms})

    inserted = entity_constraints.check_orphan_entities("default")

    assert inserted == 0


# ---------------------------------------------------------------------------
# 13. Alias uniqueness — flags two entities sharing an alias, not a single one
# ---------------------------------------------------------------------------


def test_alias_uniqueness_flags_two_entities_sharing_alias(monkeypatch):
    eid_a = _eid()
    eid_b = _eid()

    ms = MagicMock()
    ms.ping.return_value = True
    ms.execute.return_value = None

    def _fetch_all(query, params=None):
        if "HAVING count" in query:
            # One alias shared by two entities
            return [{"alias": "acme", "entity_ids": [eid_a, eid_b]}]
        if "resolved_at IS NULL" in query:
            return []
        return []

    def _fetch_one(query, params=None):
        # No open violations exist yet
        return None

    ms.fetch_all.side_effect = _fetch_all
    ms.fetch_one.side_effect = _fetch_one

    monkeypatch.setattr(_config, "_instances", {"metadata_store": ms})

    inserted = entity_constraints.check_alias_uniqueness("default")

    # Two violations inserted (one per entity)
    assert inserted == 2
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 2


def test_alias_uniqueness_no_violation_when_alias_is_unique(monkeypatch):
    ms = MagicMock()
    ms.ping.return_value = True
    ms.execute.return_value = None

    def _fetch_all(query, params=None):
        if "HAVING count" in query:
            return []  # no duplicate aliases
        return []

    ms.fetch_all.side_effect = _fetch_all

    monkeypatch.setattr(_config, "_instances", {"metadata_store": ms})

    inserted = entity_constraints.check_alias_uniqueness("default")

    assert inserted == 0


# ---------------------------------------------------------------------------
# 14. person_completeness — fires for Person with zero MENTIONS edges
# ---------------------------------------------------------------------------


def test_person_completeness_fires_for_person_with_no_mentions():
    entity_id = _eid()

    def _fetch_one(query, params=None):
        if "entity_type = 'PERSON'" in query:
            return {"entity_id": entity_id}  # is a Person
        if "relation_type = 'MENTIONS'" in query:
            return None  # zero MENTIONS edges
        if "FROM constraint_violations" in query:
            return None  # no open violation yet
        return None

    ms = MagicMock()
    ms.execute.return_value = None
    ms.fetch_one.side_effect = _fetch_one

    result = entity_constraints._check_person_completeness(ms, entity_id, "default")

    assert result is True
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 1
    # Verify INFO severity — call_args[0] is SQL, call_args[1] is the params tuple
    call_args = insert_calls[0][0]
    assert "INFO" in call_args[1]


# ---------------------------------------------------------------------------
# 15. person_completeness — does NOT fire when Person has a MENTIONS edge
# ---------------------------------------------------------------------------


def test_person_completeness_clean_when_mentions_edge_exists():
    entity_id = _eid()

    def _fetch_one(query, params=None):
        if "entity_type = 'PERSON'" in query:
            return {"entity_id": entity_id}
        if "relation_type = 'MENTIONS'" in query:
            return {"id": 99}  # has a MENTIONS edge
        return None

    ms = MagicMock()
    ms.execute.return_value = None
    ms.fetch_one.side_effect = _fetch_one

    result = entity_constraints._check_person_completeness(ms, entity_id, "default")

    assert result is False
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 0


# ---------------------------------------------------------------------------
# 16. person_completeness — does NOT fire for non-Person entities
# ---------------------------------------------------------------------------


def test_person_completeness_skips_non_person_entities():
    entity_id = _eid()

    def _fetch_one(query, params=None):
        if "entity_type = 'PERSON'" in query:
            return None  # not a Person (e.g. ORG or CONCEPT)
        return None

    ms = MagicMock()
    ms.execute.return_value = None
    ms.fetch_one.side_effect = _fetch_one

    result = entity_constraints._check_person_completeness(ms, entity_id, "default")

    assert result is False
    insert_calls = [
        c for c in ms.execute.call_args_list if "INSERT INTO constraint_violations" in c[0][0]
    ]
    assert len(insert_calls) == 0
