# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``project_entity`` writes the existing row's id into the point payload (LUM-965).

A projection row a merge moved keeps the id minted for the entry that was merged
away. The point's ``payload.entity_id`` is how readers resolve it
(``services/tools.py``, ``graph/query.py``), so it must name the row that exists.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from auth import UserContext

SRC = "11111111-1111-4111-8111-111111111111"
USER = UserContext(user_id="alice", is_authenticated=True)


def _project(existing_id):
    from services import projection

    ms = MagicMock()
    ms.fetch_one.return_value = {"entity_id": existing_id} if existing_id else None
    upserts = []
    with (
        patch.object(projection.config, "get_metadata_store", return_value=ms),
        patch.object(projection, "_embed_for_projection", return_value=[0.0]),
        patch.object(projection, "_qdrant_upsert_safe", side_effect=lambda *a: upserts.append(a)),
        patch.object(projection, "_insert_projection_or_rollback_qdrant", return_value={}),
    ):
        projection.project_entity(
            {"entity_id": SRC, "name": "Meier", "entity_type": "PERSON"},
            target_scope="shared",
            actor=USER,
        )
    return upserts[0][3]["entity_id"]


def test_U14_payload_uses_existing_row_id():
    moved = "22222222-2222-4222-8222-222222222222"
    assert _project(moved) == moved


def test_U14_payload_falls_back_to_projection_pk():
    from services.projection import projection_pk

    assert _project(None) == projection_pk("entities", SRC, "shared")
