# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres persistence for ``biography_conflict_resolutions`` (LUM-514)."""

from __future__ import annotations

import json
import logging
from uuid import UUID

import config
from models.biography_conflict import BiographyPinSnapshot
from models.biography_conflict import ConflictResolution
from models.biography_conflict import ConflictResolutionRequest
from models.biography_conflict import DetectedConflict
from ports.metadata_store import MetadataStore
from services.biography_conflict import apply_resolution_with_pins
from services.biography_conflict import detect_conflicts

_log = logging.getLogger(__name__)


def _store(store: MetadataStore | None = None) -> MetadataStore:
    return store if store is not None else config.get_metadata_store()


def _row_to_resolution(row: dict) -> ConflictResolution:
    archived = row.get("archived_pin_ids") or []
    return ConflictResolution(
        id=row["id"],
        fact_group_key=row["fact_group_key"],
        status=row["status"],
        action=row.get("resolution_action"),
        resolved_by=row.get("resolved_by"),
        resolved_at=row.get("resolved_at"),
        archived_pin_ids=list(archived),
    )


def _snapshot_from_row(row: dict) -> DetectedConflict:
    raw = row["detection_snapshot"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return DetectedConflict.model_validate(raw)


def list_conflicts(
    *,
    status: str | None = "open",
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> list[ConflictResolution]:
    ms = _store(store)
    if status:
        rows = ms.fetch_all(
            """
            SELECT id, fact_group_key, status, resolution_action,
                   resolved_by, resolved_at, archived_pin_ids
            FROM biography_conflict_resolutions
            WHERE household_instance_id = %s AND status = %s
            ORDER BY created_at ASC
            """,
            (household_instance_id, status),
        )
    else:
        rows = ms.fetch_all(
            """
            SELECT id, fact_group_key, status, resolution_action,
                   resolved_by, resolved_at, archived_pin_ids
            FROM biography_conflict_resolutions
            WHERE household_instance_id = %s
            ORDER BY created_at ASC
            """,
            (household_instance_id,),
        )
    return [_row_to_resolution(r) for r in rows]


def get_conflict(
    conflict_id: UUID,
    *,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> ConflictResolution | None:
    ms = _store(store)
    row = ms.fetch_one(
        """
        SELECT id, fact_group_key, status, resolution_action,
               resolved_by, resolved_at, archived_pin_ids
        FROM biography_conflict_resolutions
        WHERE id = %s AND household_instance_id = %s
        """,
        (str(conflict_id), household_instance_id),
    )
    return _row_to_resolution(row) if row else None


def get_conflict_detail(
    conflict_id: UUID,
    *,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> DetectedConflict | None:
    ms = _store(store)
    row = ms.fetch_one(
        """
        SELECT detection_snapshot
        FROM biography_conflict_resolutions
        WHERE id = %s AND household_instance_id = %s
        """,
        (str(conflict_id), household_instance_id),
    )
    if not row:
        return None
    return _snapshot_from_row(row)


def get_open_by_fact_group_key(
    fact_group_key: str,
    *,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> ConflictResolution | None:
    ms = _store(store)
    row = ms.fetch_one(
        """
        SELECT id, fact_group_key, status, resolution_action,
               resolved_by, resolved_at, archived_pin_ids
        FROM biography_conflict_resolutions
        WHERE household_instance_id = %s
          AND fact_group_key = %s
          AND status = 'open'
        """,
        (household_instance_id, fact_group_key),
    )
    return _row_to_resolution(row) if row else None


def insert_open_conflict(
    detected: DetectedConflict,
    *,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> ConflictResolution:
    """Insert an open conflict idempotently; return existing open row on duplicate."""
    ms = _store(store)
    snapshot = detected.model_dump(mode="json")
    pin_ids = [str(pid) for pid in detected.pin_ids]

    row = ms.fetch_one(
        """
        INSERT INTO biography_conflict_resolutions (
            household_instance_id, fact_group_key, category, domain,
            pin_ids, detection_snapshot, requires_review, status
        ) VALUES (%s, %s, %s, %s, %s::uuid[], %s::jsonb, %s, 'open')
        ON CONFLICT (fact_group_key) WHERE status = 'open'
        DO NOTHING
        RETURNING id, fact_group_key, status, resolution_action,
                  resolved_by, resolved_at, archived_pin_ids
        """,
        (
            household_instance_id,
            detected.fact_group_key,
            detected.category,
            detected.domain,
            pin_ids,
            json.dumps(snapshot),
            detected.requires_review,
        ),
    )
    if row:
        return _row_to_resolution(row)

    existing = get_open_by_fact_group_key(
        detected.fact_group_key,
        household_instance_id=household_instance_id,
        store=ms,
    )
    if existing:
        _log.debug(
            "open conflict already exists for fact_group_key=%s",
            detected.fact_group_key,
        )
        return existing
    raise RuntimeError("insert_open_conflict: insert failed without conflict row")


def detect_and_persist_open_conflicts(
    pins: list[BiographyPinSnapshot],
    *,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> list[DetectedConflict]:
    """Run detection and upsert open rows (LUM-516 synthesis integration point)."""
    ms = _store(store)
    detected_list = detect_conflicts(pins)
    for detected in detected_list:
        insert_open_conflict(
            detected,
            household_instance_id=household_instance_id,
            store=ms,
        )
    return detected_list


def resolve_conflict(
    conflict_id: UUID,
    request: ConflictResolutionRequest,
    *,
    resolved_by: str,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> ConflictResolution | None:
    """Resolve an open conflict; return None if not found; raises ValueError on bad input."""
    ms = _store(store)
    row = ms.fetch_one(
        """
        SELECT id, fact_group_key, status, resolution_action,
               resolved_by, resolved_at, archived_pin_ids, pin_ids
        FROM biography_conflict_resolutions
        WHERE id = %s AND household_instance_id = %s
        """,
        (str(conflict_id), household_instance_id),
    )
    if not row:
        return None

    current = _row_to_resolution(row)
    if current.status != "open":
        raise ConflictAlreadyClosedError(current)

    pin_ids = [UUID(str(pid)) for pid in (row.get("pin_ids") or [])]

    if request.action == "confirm_one":
        if request.chosen_pin_id is None:
            raise ValueError("chosen_pin_id required for confirm_one")
        if request.chosen_pin_id not in pin_ids:
            raise ValueError("chosen_pin_id not in conflict pin_ids")

    updated = apply_resolution_with_pins(
        current,
        pin_ids,
        request,
        resolved_by=resolved_by,
    )

    ms.execute(
        """
        UPDATE biography_conflict_resolutions
        SET status = %s,
            resolution_action = %s,
            chosen_pin_id = %s,
            archived_pin_ids = %s::uuid[],
            context_note = %s,
            resolved_by = %s,
            resolved_at = %s,
            updated_at = now()
        WHERE id = %s AND household_instance_id = %s AND status = 'open'
        """,
        (
            updated.status,
            updated.action,
            str(request.chosen_pin_id) if request.chosen_pin_id else None,
            [str(pid) for pid in updated.archived_pin_ids],
            request.context_note,
            updated.resolved_by,
            updated.resolved_at,
            str(conflict_id),
            household_instance_id,
        ),
    )

    refreshed = get_conflict(conflict_id, household_instance_id=household_instance_id, store=ms)
    if refreshed is None or refreshed.status == "open":
        raise ConflictAlreadyClosedError(refreshed or current)
    return refreshed


class ConflictAlreadyClosedError(Exception):
    """Raised when resolving a conflict that is no longer open."""

    def __init__(self, conflict: ConflictResolution) -> None:
        self.conflict = conflict
        super().__init__(f"conflict {conflict.id} is already {conflict.status}")


def seed_open_conflict_row(
    detected: DetectedConflict,
    *,
    conflict_id: UUID | None = None,
    household_instance_id: str = "default",
    store: MetadataStore | None = None,
) -> ConflictResolution:
    """Test helper: insert with optional fixed id (bypasses ON CONFLICT for seeding)."""
    ms = _store(store)
    snapshot = detected.model_dump(mode="json")
    pin_ids = [str(pid) for pid in detected.pin_ids]
    row = ms.fetch_one(
        """
        INSERT INTO biography_conflict_resolutions (
            id, household_instance_id, fact_group_key, category, domain,
            pin_ids, detection_snapshot, requires_review, status
        ) VALUES (
            COALESCE(%s::uuid, gen_random_uuid()),
            %s, %s, %s, %s, %s::uuid[], %s::jsonb, %s, 'open'
        )
        RETURNING id, fact_group_key, status, resolution_action,
                  resolved_by, resolved_at, archived_pin_ids
        """,
        (
            str(conflict_id) if conflict_id else None,
            household_instance_id,
            detected.fact_group_key,
            detected.category,
            detected.domain,
            pin_ids,
            json.dumps(snapshot),
            detected.requires_review,
        ),
    )
    if not row:
        raise RuntimeError("seed_open_conflict_row: insert failed")
    return _row_to_resolution(row)
