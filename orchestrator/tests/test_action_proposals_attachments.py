# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Propose-time attachment binding + cancel-time discard (LUM-695).

``create_proposal`` is where staged-attachment ownership is checked, because it
is the only place that can: ``model_validate`` cannot query the DB, and by
execute time there is no HTTP surface on which to return a 422.

It is also where ``proposal_id`` gets set — the column the sweep keys on.
Before it is set a staged file is "unreferenced" and ages out on the TTL; after,
it is protected for as long as the proposal stays live, which is forever, since
proposals never expire.
"""

from __future__ import annotations

import pytest
from actions import registry as actions_registry
from actions.registry import register_action
from fastapi import HTTPException
from models.actions import ActionSpec
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

import config
from services import action_proposals
from services import email_attachment_staging as staging


class _SendLike(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    attachment_ids: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    snapshot = dict(actions_registry._registry)
    register_action(
        ActionSpec(
            name="test_send_with_attachments",
            connector="smtp",
            action_type="email_send",
            handler=lambda i, *, user_id: None,
            is_write=True,
            is_reversible=False,
            input_model=_SendLike,
        )
    )
    yield
    actions_registry._registry.clear()
    actions_registry._registry.update(snapshot)


class _Store:
    def __init__(self) -> None:
        self.inserted: list[tuple] = []

    def fetch_one(self, sql, params=None):
        if "INSERT INTO action_proposals" in sql:
            self.inserted.append(params)
            return {"id": 77}
        if "UPDATE action_proposals SET status = 'rejected'" in sql:
            return {"id": params[0], "status": "rejected"}
        return None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


# ---------------------------------------------------------------------------
# Propose time
# ---------------------------------------------------------------------------


def test_create_proposal_binds_staged_attachments_to_the_new_proposal(store, monkeypatch):
    bound: dict = {}
    monkeypatch.setattr(staging, "resolve_all", lambda uid, ids: [object() for _ in ids])
    monkeypatch.setattr(
        staging,
        "bind_to_proposal",
        lambda uid, ids, *, proposal_id: bound.update(
            {"user": uid, "ids": list(ids), "proposal": proposal_id}
        ),
    )

    pid = action_proposals.create_proposal(
        "test_send_with_attachments", {"body": "hi", "attachment_ids": ["a", "b"]}, user_id="u1"
    )

    assert pid == 77
    assert bound == {"user": "u1", "ids": ["a", "b"], "proposal": 77}


def test_send_rejects_an_unowned_attachment_id_with_422(store, monkeypatch):
    """The ownership check a Pydantic model could not do."""

    def _gone(uid, ids):
        raise staging.StagedAttachmentUnavailable("not yours")

    monkeypatch.setattr(staging, "resolve_all", _gone)

    with pytest.raises(HTTPException) as exc:
        action_proposals.create_proposal(
            "test_send_with_attachments",
            {"body": "hi", "attachment_ids": ["foreign"]},
            user_id="u1",
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "attachment_unavailable"
    assert store.inserted == [], "no proposal may be created for an unavailable attachment"


def test_a_payload_without_attachment_ids_never_consults_staging(store, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("staging must not be consulted")

    monkeypatch.setattr(staging, "resolve_all", _boom)
    monkeypatch.setattr(staging, "bind_to_proposal", _boom)

    assert (
        action_proposals.create_proposal("test_send_with_attachments", {"body": "hi"}, user_id="u1")
        == 77
    )


def test_a_bind_failure_degrades_rather_than_losing_the_proposal(store, monkeypatch):
    """Unreferenced files age out on the TTL — degraded, but not a lost send."""
    monkeypatch.setattr(staging, "resolve_all", lambda uid, ids: [object()])

    def _fail(*a, **k):
        raise RuntimeError("db blip")

    monkeypatch.setattr(staging, "bind_to_proposal", _fail)

    assert (
        action_proposals.create_proposal(
            "test_send_with_attachments", {"body": "hi", "attachment_ids": ["a"]}, user_id="u1"
        )
        == 77
    )


# ---------------------------------------------------------------------------
# Cancel time
# ---------------------------------------------------------------------------


def test_cancelled_proposal_discards_its_staged_files(store, monkeypatch):
    """Backing out of a send returns the disk promptly, not at the next sweep."""
    discarded: list[int] = []
    monkeypatch.setattr(staging, "discard_for_proposal", lambda pid: discarded.append(pid) or 1)

    action_proposals.cancel_proposal(77, user_id="u1")

    assert discarded == [77]


def test_a_discard_failure_does_not_fail_the_cancel(store, monkeypatch):
    def _fail(pid):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(staging, "discard_for_proposal", _fail)

    out = action_proposals.cancel_proposal(77, user_id="u1")

    assert out["status"] == "rejected"
