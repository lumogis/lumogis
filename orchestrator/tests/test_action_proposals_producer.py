# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the Ask/Do proposal producer (LUM-623).

``services.action_proposals`` is the connector-agnostic enqueue/approve half of
the act tier. These tests use a functional in-memory fake ``MetadataStore`` that
understands the producer's three SQL shapes, plus throwaway registered actions.
"""

from __future__ import annotations

import json

import pytest
from actions.registry import register_action
from fastapi import HTTPException
from models.actions import ActionSpec
from pydantic import BaseModel
from pydantic import ConfigDict

import config
from services import action_proposals


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thing: str


class FakeStore:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._seq = 0

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        if q.startswith("insert into action_proposals"):
            self._seq += 1
            uid, action_name, payload_json = p[0], p[1], p[2]
            self.rows[self._seq] = {
                "id": self._seq,
                "user_id": uid,
                "action_name": action_name,
                "payload": json.loads(payload_json),
                "status": "pending",
            }
            return {"id": self._seq}
        if q.startswith("update action_proposals set status = 'approved'"):
            pid, uid = int(p[0]), p[1]
            r = self.rows.get(pid)
            if r and r["user_id"] == uid and r["status"] == "pending":
                r["status"] = "approved"
                return {"id": pid, "status": "approved"}
            return None
        if q.startswith("update action_proposals set status = 'rejected'"):
            pid, uid = int(p[0]), p[1]
            r = self.rows.get(pid)
            if r and r["user_id"] == uid and r["status"] == "pending":
                r["status"] = "rejected"
                return {"id": pid, "status": "rejected"}
            return None
        return None

    def fetch_all(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        if "from action_proposals" in q and "status = 'pending'" in q:
            uid = p[0]
            return [
                dict(r, created_at=None)
                for r in self.rows.values()
                if r["user_id"] == uid and r["status"] == "pending"
            ]
        return []


@pytest.fixture
def store():
    s = FakeStore()
    config._instances["metadata_store"] = s
    # A benign reversible test action with an input_model, and a hard-limited one.
    register_action(
        ActionSpec(
            name="_t_producer_ok",
            connector="_t",
            action_type="test_write",
            is_write=True,
            is_reversible=True,
            handler=lambda inp, **kw: None,
            input_model=_Payload,
        )
    )
    register_action(
        ActionSpec(
            name="_t_producer_hardlimited",
            connector="_t",
            action_type="mass_communication",  # in _HARD_LIMITED
            is_write=True,
            is_reversible=False,
            handler=lambda inp, **kw: None,
        )
    )
    # An internal-only action (no input_model) that is NOT hard-limited — models
    # the reverse action. It must be rejected by the producer as not_proposable.
    register_action(
        ActionSpec(
            name="_t_producer_no_model",
            connector="_t",
            action_type="test_write",
            is_write=True,
            is_reversible=False,
            handler=lambda inp, **kw: None,
            input_model=None,
        )
    )
    yield s
    config._instances.pop("metadata_store", None)


def test_create_then_approve(store):
    pid = action_proposals.create_proposal("_t_producer_ok", {"thing": "x"}, user_id="u1")
    assert store.rows[pid]["status"] == "pending"
    out = action_proposals.approve_proposal(pid, user_id="u1")
    assert out == {"proposal_id": pid, "status": "approved"}
    assert store.rows[pid]["status"] == "approved"


def test_cancel_proposal_owner_guard_and_state(store):
    # LUM-682: a user abandoning an unsent proposal → pending → rejected.
    pid = action_proposals.create_proposal("_t_producer_ok", {"thing": "x"}, user_id="u1")
    # Another user cannot cancel it (404, no oracle).
    with pytest.raises(HTTPException) as ei:
        action_proposals.cancel_proposal(pid, user_id="u2")
    assert ei.value.status_code == 404
    assert store.rows[pid]["status"] == "pending"  # untouched
    # The owner can.
    out = action_proposals.cancel_proposal(pid, user_id="u1")
    assert out == {"proposal_id": pid, "status": "rejected"}
    assert store.rows[pid]["status"] == "rejected"
    # Cancelling again (no longer pending) → 404.
    with pytest.raises(HTTPException) as ei2:
        action_proposals.cancel_proposal(pid, user_id="u1")
    assert ei2.value.status_code == 404


def test_list_pending_filters_by_user(store):
    action_proposals.create_proposal("_t_producer_ok", {"thing": "a"}, user_id="u1")
    action_proposals.create_proposal("_t_producer_ok", {"thing": "b"}, user_id="u2")
    rows = action_proposals.list_pending("u1")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"thing": "a"}


def test_approve_other_users_proposal_404(store):
    pid = action_proposals.create_proposal("_t_producer_ok", {"thing": "x"}, user_id="u1")
    with pytest.raises(HTTPException) as exc:
        action_proposals.approve_proposal(pid, user_id="u2")  # not the owner
    assert exc.value.status_code == 404
    assert store.rows[pid]["status"] == "pending"  # untouched


def test_create_hard_limited_403(store):
    with pytest.raises(HTTPException) as exc:
        action_proposals.create_proposal("_t_producer_hardlimited", {}, user_id="u1")
    assert exc.value.status_code == 403


def test_create_unknown_action_400(store):
    with pytest.raises(HTTPException) as exc:
        action_proposals.create_proposal("_t_does_not_exist", {}, user_id="u1")
    assert exc.value.status_code == 400


def test_create_internal_only_action_rejected_400(store):
    # An action with no input_model (e.g. the imap_writeback_reverse inverse) is
    # internal-only and must not be creatable via the generic producer, else a
    # caller could hand-craft a reverse payload that bypasses signal attribution
    # (code-review #1).
    with pytest.raises(HTTPException) as exc:
        action_proposals.create_proposal("_t_producer_no_model", {"any": "thing"}, user_id="u1")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "not_proposable"


def test_create_rejects_smuggled_field_422(store):
    # extra="forbid" on the action's input_model rejects an unexpected key
    # (e.g. a smuggled user_id) at the 422 layer.
    with pytest.raises(HTTPException) as exc:
        action_proposals.create_proposal(
            "_t_producer_ok", {"thing": "x", "user_id": "victim"}, user_id="u1"
        )
    assert exc.value.status_code == 422
