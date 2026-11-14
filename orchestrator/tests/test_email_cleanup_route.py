# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-646 — route tests for the inbox-cleanup endpoints.

Owner-scoping (404), rate limiting (429), CSRF-guarded 202s, and CleanupError → HTTP
status mapping. The email_cleanup service is stubbed so the routes are tested in isolation.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_auth_phase1 import FakeUsersStore

_SID = "11111111-1111-1111-1111-111111111111"


class _Store(FakeUsersStore):
    @staticmethod
    def _norm(q):
        return " ".join(q.split()).lower()

    def fetch_one(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if "select id::text as id from sources" in q and "source_type = 'imap'" in q:
            sid, uid = p
            return {"id": _SID} if sid == _SID and uid == "default" else None
        return super().fetch_one(query, params)


@pytest.fixture
def env(monkeypatch):
    import config as _config

    store = _Store()
    _config._instances["metadata_store"] = store
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    # reset the module-global rate limiter so counts don't leak across tests
    from routes import signals as sroutes

    sroutes._email_cleanup_limiter._hits.clear() if hasattr(
        sroutes._email_cleanup_limiter, "_hits"
    ) else None
    yield store
    _config._instances.pop("metadata_store", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def _patch(monkeypatch, **fns):
    from services import email_cleanup

    for name, fn in fns.items():
        monkeypatch.setattr(email_cleanup, name, fn)


def test_start_returns_202(env, monkeypatch):
    _patch(monkeypatch, start_job=lambda *a, **k: {"job_id": "j1", "state": "backfilling"})
    with _client() as client:
        resp = client.post(
            f"/me/email-sources/{_SID}/cleanup",
            json={"window_start": "2024-01-01", "window_end": "2024-01-31"},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"] == "j1"


def test_start_invalid_window_422(env):
    with _client() as client:
        resp = client.post(
            f"/me/email-sources/{_SID}/cleanup",
            json={"window_start": "2024-02-01", "window_end": "2024-01-01"},
        )
    assert resp.status_code == 422, resp.text


def test_start_unowned_source_404(env, monkeypatch):
    _patch(monkeypatch, start_job=lambda *a, **k: {"job_id": "j1", "state": "backfilling"})
    with _client() as client:
        resp = client.post(
            "/me/email-sources/99999999-9999-9999-9999-999999999999/cleanup", json={}
        )
    assert resp.status_code == 404


def test_start_rate_limited_429(env, monkeypatch):
    from routes import signals as sroutes

    monkeypatch.setattr(sroutes._email_cleanup_limiter, "check", lambda uid: False)
    with _client() as client:
        resp = client.post(f"/me/email-sources/{_SID}/cleanup", json={})
    assert resp.status_code == 429


def test_approve_job_not_ready_maps_409(env, monkeypatch):
    from services.email_cleanup import CleanupError

    def _raise(*a, **k):
        raise CleanupError("job_not_ready", 409)

    _patch(monkeypatch, approve=_raise)
    with _client() as client:
        resp = client.post(
            f"/me/email-sources/{_SID}/cleanup/j1/approve",
            json={"actions": [{"category": "promotional_bulk", "move": True}]},
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "job_not_ready"


def test_approve_ok_202(env, monkeypatch):
    _patch(monkeypatch, approve=lambda *a, **k: {"approved": 1})
    with _client() as client:
        resp = client.post(
            f"/me/email-sources/{_SID}/cleanup/j1/approve",
            json={"actions": [{"category": "promotional_bulk", "move": True}]},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["approved"] == 1


def test_get_summary_200(env, monkeypatch):
    _patch(
        monkeypatch,
        job_summary=lambda *a, **k: {
            "state": "ready",
            "kind": "window",
            "progress": 1.0,
            "proposals": [],
        },
    )
    with _client() as client:
        resp = client.get(f"/me/email-sources/{_SID}/cleanup/j1")
    assert resp.status_code == 200
    assert resp.json()["state"] == "ready"


def test_undo_202(env, monkeypatch):
    _patch(monkeypatch, undo_job=lambda *a, **k: {"undo_pending": True})
    with _client() as client:
        resp = client.post(f"/me/email-sources/{_SID}/cleanup/j1/undo", json={"ops": ["move"]})
    assert resp.status_code == 202, resp.text


def test_spam_get_200_and_post_202(env, monkeypatch):
    _patch(
        monkeypatch,
        spam_folder_count=lambda *a, **k: {"spam_count": 12, "trash_available": True},
        start_spam_job=lambda *a, **k: {"job_id": "s1", "state": "backfilling"},
    )
    with _client() as client:
        g = client.get(f"/me/email-sources/{_SID}/cleanup-spam")
        p = client.post(f"/me/email-sources/{_SID}/cleanup-spam")
    assert g.status_code == 200 and g.json()["spam_count"] == 12
    assert p.status_code == 202 and p.json()["job_id"] == "s1"


def test_spam_get_rate_limited_429(env, monkeypatch):
    # The spam GET opens a read-only IMAP session, so it is rate-limited like the other
    # IMAP-touching endpoints.
    from routes import signals as sroutes

    monkeypatch.setattr(sroutes._email_cleanup_limiter, "check", lambda uid: False)
    with _client() as client:
        resp = client.get(f"/me/email-sources/{_SID}/cleanup-spam")
    assert resp.status_code == 429


def test_spam_post_no_folder_409(env, monkeypatch):
    from services.email_cleanup import CleanupError

    def _raise(*a, **k):
        raise CleanupError("no_spam_folder", 409)

    _patch(monkeypatch, start_spam_job=_raise)
    with _client() as client:
        resp = client.post(f"/me/email-sources/{_SID}/cleanup-spam")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "no_spam_folder"
