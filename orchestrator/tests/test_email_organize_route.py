# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-645 — route tests for the opt-in organization endpoints.

PATCH /me/email-sources/{id}/organize        -> 202, owner-scoped, validated
POST  /me/email-sources/{id}/organize/undo   -> 202, sets undo scope
GET   /me/email-sources/{id}/organize/log    -> owner's activity log
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_auth_phase1 import FakeUsersStore

_SID = "11111111-1111-1111-1111-111111111111"


class _Store(FakeUsersStore):
    def __init__(self):
        super().__init__()
        # one owned imap source for the default user
        self.src = {
            "id": _SID,
            "user_id": "default",
            "organize_label_enabled": False,
            "organize_folder_enabled": False,
            "organize_folder_categories": None,
            "organize_since": None,
            "organize_undo_scope": None,
        }
        self.log = []

    @staticmethod
    def _norm(q):
        return " ".join(q.split()).lower()

    def fetch_one(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if "id::text as id" in q and "from sources" in q and "source_type = 'imap'" in q:
            sid, uid = p
            if sid != _SID or uid != "default":
                return None
            # LUM-729 widened the organize route's ownership read to carry the
            # CURRENT folder-sort opt-in, because the gate fires on the OFF→ON
            # transition and needs the value before the write. Other routes still
            # use the narrow read, so both shapes arrive here.
            return {"id": _SID, "folder_on": bool(self.src["organize_folder_enabled"])}
        return super().fetch_one(query, params)

    def fetch_all(self, query, params=None):
        q = self._norm(query)
        if "from email_organize_log where source_id" in q:
            return list(self.log)
        try:
            return super().fetch_all(query, params)
        except Exception:
            return []

    def execute(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if "update sources set organize_label_enabled" in q:
            self.src["organize_label_enabled"] = p[0]
            self.src["organize_folder_enabled"] = p[1]
            self.src["organize_folder_categories"] = p[2]
            return
        if "update sources set organize_undo_scope" in q:
            self.src["organize_undo_scope"] = p[0]
            return
        return super().execute(query, params)


@pytest.fixture
def env(monkeypatch):
    import config as _config

    store = _Store()
    _config._instances["metadata_store"] = store
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    yield store
    _config._instances.pop("metadata_store", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def test_set_organize_returns_202_and_persists(env):
    with _client() as client:
        resp = client.patch(
            f"/me/email-sources/{_SID}/organize",
            json={
                "organize_label": True,
                "organize_folder": True,
                "organize_folder_categories": ["promotional_bulk"],
            },
        )
    assert resp.status_code == 202, resp.text
    assert env.src["organize_label_enabled"] is True
    assert env.src["organize_folder_enabled"] is True


def test_folder_categories_without_folder_is_422(env):
    with _client() as client:
        resp = client.patch(
            f"/me/email-sources/{_SID}/organize",
            json={
                "organize_label": False,
                "organize_folder": False,
                "organize_folder_categories": ["promotional_bulk"],
            },
        )
    assert resp.status_code == 422, resp.text


def test_other_is_stripped_from_folder_categories(env):
    with _client() as client:
        resp = client.patch(
            f"/me/email-sources/{_SID}/organize",
            json={
                "organize_label": False,
                "organize_folder": True,
                "organize_folder_categories": ["other", "promotional_bulk"],
            },
        )
    assert resp.status_code == 202, resp.text


def test_organize_on_unowned_source_is_404(env):
    with _client() as client:
        resp = client.patch(
            "/me/email-sources/99999999-9999-9999-9999-999999999999/organize",
            json={"organize_label": True, "organize_folder": False},
        )
    assert resp.status_code == 404


def test_undo_returns_202_and_sets_scope(env):
    with _client() as client:
        resp = client.post(f"/me/email-sources/{_SID}/organize/undo", json={"scope": "all"})
    assert resp.status_code == 202, resp.text
    assert env.src["organize_undo_scope"] == "all"


def test_log_returns_owner_entries(env):
    env.log = [
        {
            "signal_url": "imap://x/1/1",
            "op": "label",
            "target": "Lumogis/Bills",
            "label_kind": "gmail",
            "applied_at": "t",
            "reversed_at": None,
        }
    ]
    with _client() as client:
        resp = client.get(f"/me/email-sources/{_SID}/organize/log")
    assert resp.status_code == 200
    assert resp.json()["entries"][0]["op"] == "label"
