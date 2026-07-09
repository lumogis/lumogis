# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""API tests for ``/api/v1/biography/conflicts`` (LUM-514)."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from models.biography_conflict import ConflictContribution
from models.biography_conflict import DetectedConflict
from services import biography_conflict_store as store

_TS = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-biography-conflicts-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def _sample_detected(*, fact_group_key: str = "logistics|household|dinner time") -> DetectedConflict:
    pin_a, pin_b = uuid.uuid4(), uuid.uuid4()
    return DetectedConflict(
        fact_group_key=fact_group_key,
        category="logistics",
        domain="household",
        pin_ids=[pin_a, pin_b],
        contributions=[
            ConflictContribution(user_id="alice", pin_id=pin_a, text="18:00", updated_at=_TS),
            ConflictContribution(user_id="bob", pin_id=pin_b, text="19:00", updated_at=_TS),
        ],
        requires_review=True,
        represent_both_line="alice: 18:00 · bob: 19:00",
    )


class _BiographyConflictMemoryStore:
    """Minimal in-memory MetadataStore for biography_conflict_resolutions SQL."""

    def __init__(self) -> None:
        self._rows: dict[UUID, dict[str, Any]] = {}

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split())
        params = params or ()
        if q.startswith("UPDATE biography_conflict_resolutions"):
            cid = UUID(params[-2])
            row = self._rows.get(cid)
            if not row or row["status"] != "open":
                return
            row.update(
                {
                    "status": params[0],
                    "resolution_action": params[1],
                    "chosen_pin_id": UUID(params[2]) if params[2] else None,
                    "archived_pin_ids": [UUID(x) for x in params[3]],
                    "context_note": params[4],
                    "resolved_by": params[5],
                    "resolved_at": params[6],
                }
            )

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split())
        params = params or ()

        if "INSERT INTO biography_conflict_resolutions" in q:
            if "COALESCE" in q:
                cid = UUID(params[0]) if params[0] else uuid.uuid4()
            elif "ON CONFLICT" in q:
                fact_key = params[1]
                for row in self._rows.values():
                    if row["status"] == "open" and row["fact_group_key"] == fact_key:
                        return None
                cid = uuid.uuid4()
            else:
                cid = uuid.uuid4()

            pin_ids = [UUID(x) for x in params[4]] if "COALESCE" not in q else [UUID(x) for x in params[5]]
            snapshot_raw = params[5] if "COALESCE" not in q else params[6]
            requires_review = params[6] if "COALESCE" not in q else params[7]
            fact_key = params[1] if "COALESCE" not in q else params[2]
            category = params[2] if "COALESCE" not in q else params[3]
            domain = params[3] if "COALESCE" not in q else params[4]

            row = {
                "id": cid,
                "household_instance_id": "default",
                "fact_group_key": fact_key,
                "category": category,
                "domain": domain,
                "pin_ids": pin_ids,
                "detection_snapshot": json.loads(snapshot_raw),
                "requires_review": requires_review,
                "status": "open",
                "resolution_action": None,
                "chosen_pin_id": None,
                "archived_pin_ids": [],
                "context_note": None,
                "resolved_by": None,
                "resolved_at": None,
            }
            self._rows[cid] = row
            return {
                "id": cid,
                "fact_group_key": fact_key,
                "status": "open",
                "resolution_action": None,
                "resolved_by": None,
                "resolved_at": None,
                "archived_pin_ids": [],
            }

        if "WHERE id = %s AND household_instance_id = %s AND status = 'open'" in q:
            cid = UUID(params[0])
            row = self._rows.get(cid)
            if not row or row["household_instance_id"] != params[1]:
                return None
            return dict(row)

        if "WHERE id = %s AND household_instance_id = %s" in q:
            cid = UUID(params[0])
            row = self._rows.get(cid)
            if not row or row["household_instance_id"] != params[1]:
                return None
            if "detection_snapshot" in q and "pin_ids" not in q:
                return {"detection_snapshot": row["detection_snapshot"]}
            return {
                "id": row["id"],
                "fact_group_key": row["fact_group_key"],
                "status": row["status"],
                "resolution_action": row["resolution_action"],
                "resolved_by": row["resolved_by"],
                "resolved_at": row["resolved_at"],
                "archived_pin_ids": row["archived_pin_ids"],
                "pin_ids": row["pin_ids"],
            }

        if "fact_group_key = %s" in q and "status = 'open'" in q:
            for row in self._rows.values():
                if (
                    row["household_instance_id"] == params[0]
                    and row["fact_group_key"] == params[1]
                    and row["status"] == "open"
                ):
                    return {
                        "id": row["id"],
                        "fact_group_key": row["fact_group_key"],
                        "status": row["status"],
                        "resolution_action": row["resolution_action"],
                        "resolved_by": row["resolved_by"],
                        "resolved_at": row["resolved_at"],
                        "archived_pin_ids": row["archived_pin_ids"],
                    }
            return None

        if "WHERE household_instance_id = %s AND status = %s" in q:
            out = []
            for row in self._rows.values():
                if row["household_instance_id"] == params[0] and row["status"] == params[1]:
                    out.append(row)
            return out[0] if len(out) == 1 and "ORDER BY" not in q else None

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split())
        params = params or ()
        if "WHERE household_instance_id = %s AND status = %s" in q:
            rows = [
                r
                for r in self._rows.values()
                if r["household_instance_id"] == params[0] and r["status"] == params[1]
            ]
            return [
                {
                    "id": r["id"],
                    "fact_group_key": r["fact_group_key"],
                    "status": r["status"],
                    "resolution_action": r["resolution_action"],
                    "resolved_by": r["resolved_by"],
                    "resolved_at": r["resolved_at"],
                    "archived_pin_ids": r["archived_pin_ids"],
                }
                for r in sorted(rows, key=lambda x: x["id"])
            ]
        return []


@contextmanager
def _memory_store(monkeypatch: pytest.MonkeyPatch):
    mem = _BiographyConflictMemoryStore()

    def _fake_get_metadata_store():
        return mem

    monkeypatch.setattr(store.config, "get_metadata_store", _fake_get_metadata_store)
    yield mem


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def test_list_conflicts_requires_auth(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-bio-401")
    with _memory_store(monkeypatch):
        r = client.get("/api/v1/biography/conflicts")
    assert r.status_code == 401


def test_list_conflicts_200_user(client, monkeypatch) -> None:
    detected = _sample_detected()
    hdr = _auth_header(monkeypatch, "alice", "user")
    with _memory_store(monkeypatch) as mem:
        store.seed_open_conflict_row(detected, store=mem)
        r = client.get("/api/v1/biography/conflicts", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["status"] == "open"


def test_get_conflict_detail(client, monkeypatch) -> None:
    detected = _sample_detected()
    hdr = _auth_header(monkeypatch, "alice", "user")
    with _memory_store(monkeypatch) as mem:
        row = store.seed_open_conflict_row(detected, store=mem)
        r = client.get(f"/api/v1/biography/conflicts/{row.id}", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["represent_both_line"] == "alice: 18:00 · bob: 19:00"


def test_resolve_requires_admin(client, monkeypatch) -> None:
    detected = _sample_detected()
    hdr = _auth_header(monkeypatch, "bob", "user")
    with _memory_store(monkeypatch) as mem:
        row = store.seed_open_conflict_row(detected, store=mem)
        r = client.post(
            f"/api/v1/biography/conflicts/{row.id}/resolve",
            headers=hdr,
            json={"action": "dismiss"},
        )
    assert r.status_code == 403


def test_confirm_one_missing_chosen_id(client, monkeypatch) -> None:
    detected = _sample_detected()
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    with _memory_store(monkeypatch) as mem:
        row = store.seed_open_conflict_row(detected, store=mem)
        r = client.post(
            f"/api/v1/biography/conflicts/{row.id}/resolve",
            headers=hdr,
            json={"action": "confirm_one"},
        )
    assert r.status_code == 400


def test_chosen_pin_not_in_conflict(client, monkeypatch) -> None:
    detected = _sample_detected()
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    with _memory_store(monkeypatch) as mem:
        row = store.seed_open_conflict_row(detected, store=mem)
        r = client.post(
            f"/api/v1/biography/conflicts/{row.id}/resolve",
            headers=hdr,
            json={"action": "confirm_one", "chosen_pin_id": str(uuid.uuid4())},
        )
    assert r.status_code == 400


def test_resolve_confirm_one_success(client, monkeypatch) -> None:
    detected = _sample_detected()
    chosen = detected.pin_ids[0]
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    with _memory_store(monkeypatch) as mem:
        row = store.seed_open_conflict_row(detected, store=mem)
        r = client.post(
            f"/api/v1/biography/conflicts/{row.id}/resolve",
            headers=hdr,
            json={"action": "confirm_one", "chosen_pin_id": str(chosen)},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert str(detected.pin_ids[1]) in [str(x) for x in body["archived_pin_ids"]]


def test_resolve_already_closed_409(client, monkeypatch) -> None:
    detected = _sample_detected()
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    with _memory_store(monkeypatch) as mem:
        row = store.seed_open_conflict_row(detected, store=mem)
        first = client.post(
            f"/api/v1/biography/conflicts/{row.id}/resolve",
            headers=hdr,
            json={"action": "dismiss"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/v1/biography/conflicts/{row.id}/resolve",
            headers=hdr,
            json={"action": "dismiss"},
        )
    assert second.status_code == 409
