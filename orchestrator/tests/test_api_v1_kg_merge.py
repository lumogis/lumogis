# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/kg`` merge, history, undo and move-mentions routes (LUM-965).

The service is mocked; this file pins the HTTP contract (status codes, error
codes, ``which``, request validation, CSRF). What the service and the SQL do is
proven in ``tests/integration/test_entity_merge_undo_live.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
import routes.api_v1.kg as kgmod
from auth import UserContext
from authz import require_user
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import entity_edit

A = str(uuid.uuid4())
B = str(uuid.uuid4())
MID = str(uuid.uuid4())
ORIGIN = {"Origin": "http://testserver"}
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def _row(eid, name="Dr. Meier", version=4):
    return {
        "entity_id": eid,
        "name": name,
        "entity_type": "PERSON",
        "aliases": ["A. Meier"],
        "user_id": "alice",
        "scope": "personal",
        "published_from": None,
        "version": version,
        "summary": "",
    }


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(kgmod.router)
    ctx = UserContext(user_id="alice", is_authenticated=True, role="user", allows_shared=True)
    app.dependency_overrides[require_user] = lambda: ctx
    monkeypatch.setattr(kgmod, "get_user", lambda request: ctx)
    monkeypatch.setattr(kgmod, "_effective_allows_shared", lambda u: True)
    monkeypatch.setattr(kgmod, "_fetch_shared_entity_source_ids", lambda *a, **k: set())
    return TestClient(app)


BODY = {"other_id": B, "version": 4, "other_version": 2, "keep_name": "this"}


def _post_merge(client, **over):
    return client.post(f"/api/v1/kg/entities/{A}/merge", json={**BODY, **over}, headers=ORIGIN)


def test_R1_merge_200_shape(client):
    with patch.object(entity_edit, "merge_entries", return_value=(_row(A, version=5), MID)) as m:
        resp = _post_merge(client, keep_name="other")
    assert resp.status_code == 200
    data = resp.json()
    assert data["merge_id"] == MID and data["entry"]["entity_id"] == A
    assert data["entry"]["version"] == 5
    kw = m.call_args.kwargs
    assert (kw["version"], kw["other_version"], kw["keep_name"]) == (4, 2, "other")
    assert m.call_args.args[1:] == (A, B)


@pytest.mark.parametrize(
    "exc,status,code,extra",
    [
        (entity_edit.EntitySameEntry(), 422, "same_entry", {}),
        (entity_edit.EntityStaged("other"), 422, "staged_entry", {"which": "other"}),
        (entity_edit.EntityNotFound(B, "other"), 404, "entity_not_found", {"which": "other"}),
        (entity_edit.EntityNotOwner(B, "other"), 403, "not_owner", {"which": "other"}),
        (
            entity_edit.EntityNotEditable("src-1", "other"),
            403,
            "not_editable",
            {"which": "other", "source_entity_id": "src-1"},
        ),
        (
            entity_edit.EntityVersionConflict(9, "other"),
            409,
            "version_conflict",
            {"which": "other", "current_version": 9},
        ),
        (entity_edit.EntityStoreUnavailable("x"), 503, "store_unavailable", {}),
    ],
)
def test_R2_to_R6_merge_errors(client, exc, status, code, extra):
    with patch.object(entity_edit, "merge_entries", side_effect=exc):
        resp = _post_merge(client)
    assert resp.status_code == status
    detail = resp.json()["detail"]
    assert detail["code"] == code and detail["error"] == code
    for k, v in extra.items():
        assert detail[k] == v


def test_R7_write_routes_need_same_origin():
    from csrf import require_same_origin

    writes = [
        r
        for r in kgmod.router.routes
        if "POST" in r.methods and r.path.endswith(("/merge", "/undo", "/mentions/move"))
    ]
    assert len(writes) == 3
    for route in writes:
        assert require_same_origin in [d.dependency for d in route.dependencies], route.path
    reads = [r for r in kgmod.router.routes if r.path.endswith("/history")]
    assert reads and not any(
        require_same_origin in [d.dependency for d in r.dependencies] for r in reads
    )


def test_R8_all_routes_sit_behind_require_user():
    routes = {
        (r.path, tuple(sorted(r.methods)))
        for r in kgmod.router.routes
        if r.path.endswith(("/merge", "/history", "/undo", "/mentions/move"))
    }
    assert len(routes) == 4
    assert require_user in [d.dependency for d in kgmod.router.dependencies]


def test_R9_history_shape_and_404(client):
    item = {
        "merge_id": MID,
        "direction": "merged_in",
        "other_entity_id": B,
        "other_name": "Dr. A. Meier",
        "origin": "auto_dedup",
        "merged_at": NOW,
        "undone_at": None,
        "can_undo": True,
        "newer_merge_id": None,
        "newer_other_name": None,
        "restores_sharing": False,
    }
    with patch.object(entity_edit, "list_merge_history", return_value=[item]):
        resp = client.get(f"/api/v1/kg/entities/{A}/history")
    assert resp.status_code == 200
    got = resp.json()["items"][0]
    assert got["origin"] == "auto_dedup" and got["can_undo"] is True
    assert "newer_other_name" not in got
    with patch.object(entity_edit, "list_merge_history", side_effect=entity_edit.EntityNotFound(A)):
        assert client.get(f"/api/v1/kg/entities/{A}/history").status_code == 404


def test_R10_undo_200_and_errors(client):
    since = [
        {
            "kind": "document",
            "ref_id": "1",
            "title": "Letter",
            "mentioned_at": NOW,
            "relation_id": 77,
        }
    ]
    from models.api_v1 import EntityMovableMention

    mentions = [EntityMovableMention(**s) for s in since]
    with patch.object(
        entity_edit, "undo_merge", return_value=(_row(B, "Dr. A. Meier"), _row(A), mentions, 2)
    ):
        resp = client.post(f"/api/v1/kg/merges/{MID}/undo", headers=ORIGIN)
    data = resp.json()
    assert resp.status_code == 200 and data["skipped_rows"] == 2
    assert data["mentions_since"][0]["relation_id"] == 77
    assert data["restored"]["entity_id"] == B and data["survivor"]["entity_id"] == A

    for exc, status, code, extra in [
        (entity_edit.MergeNotFound("x"), 404, "merge_not_found", {}),
        (entity_edit.MergeAlreadyUndone("x"), 409, "already_undone", {}),
        (
            entity_edit.MergeNotNewest("m2", "Dr. A. Meier"),
            409,
            "undo_newer_first",
            {"newer_merge_id": "m2", "newer_other_name": "Dr. A. Meier"},
        ),
        (entity_edit.MergeLoserIdTaken("x"), 409, "loser_id_taken", {}),
        (entity_edit.EntityStoreUnavailable("x"), 503, "store_unavailable", {}),
    ]:
        with patch.object(entity_edit, "undo_merge", side_effect=exc):
            resp = client.post(f"/api/v1/kg/merges/{MID}/undo", headers=ORIGIN)
        assert resp.status_code == status
        assert resp.json()["detail"]["code"] == code
        for k, v in extra.items():
            assert resp.json()["detail"][k] == v


def test_R10b_malformed_merge_id_is_404_not_500(client):
    with patch("services.entity_edit.config.get_metadata_store") as ms:
        resp = client.post("/api/v1/kg/merges/not-a-uuid/undo", headers=ORIGIN)
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "merge_not_found"
    ms.assert_not_called()


MOVE = {"to_entity_id": B, "relation_ids": [1, 2], "version": 4, "to_version": 2}


def test_R11_move_200_and_errors(client):
    with patch.object(entity_edit, "move_mentions", return_value=(_row(A), _row(B), 2)) as m:
        resp = client.post(f"/api/v1/kg/entities/{A}/mentions/move", json=MOVE, headers=ORIGIN)
    assert resp.status_code == 200
    data = resp.json()
    assert data["moved"] == 2 and data["source"]["entity_id"] == A
    assert data["target"]["entity_id"] == B
    assert m.call_args.kwargs["relation_ids"] == [1, 2]

    with patch.object(entity_edit, "move_mentions", side_effect=entity_edit.MentionsNotFound()):
        resp = client.post(f"/api/v1/kg/entities/{A}/mentions/move", json=MOVE, headers=ORIGIN)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "relation_not_found"
    with patch.object(entity_edit, "move_mentions", side_effect=entity_edit.EntitySameEntry()):
        resp = client.post(f"/api/v1/kg/entities/{A}/mentions/move", json=MOVE, headers=ORIGIN)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "same_entry"
    for bad in ([], list(range(1, 202))):
        resp = client.post(
            f"/api/v1/kg/entities/{A}/mentions/move",
            json={**MOVE, "relation_ids": bad},
            headers=ORIGIN,
        )
        assert resp.status_code == 422


def test_R12_extra_fields_are_refused(client):
    resp = _post_merge(client, winner_id=B)
    assert resp.status_code == 422
    resp = _post_merge(client, version=0)
    assert resp.status_code == 422
    resp = _post_merge(client, keep_name="both")
    assert resp.status_code == 422
