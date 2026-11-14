# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for PATCH /sources/{id} — IMAP mode toggle (LUM-628).

TestClient + fake metadata store + monkeypatched ``schedule_source`` spy — no
live IMAP, DB, or APScheduler. Reuses the LUM-170 email-route fake harness and
extends the store with an ``UPDATE sources SET extraction_method`` branch.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_email_source_routes import _EmailRoutesStore
from tests.test_email_source_routes import _FakeScheduler


class _ModeStore(_EmailRoutesStore):
    """Adds the source-UPDATE branch to the email-route fake store.

    LUM-837 widened ``PATCH /sources/{id}`` beyond the mode toggle, so the SET
    clause is now assembled from whichever allowlisted columns the request
    supplied. This parses the clause generically rather than matching one fixed
    statement — which also means a route that smuggled an unexpected column into
    the SQL would be visible here rather than silently applied.

    Shared by ``test_source_update.py``: one fake for one route.
    """

    # The only columns the edit route may write, mirroring
    # `routes.signals._SOURCE_UPDATE_COLUMNS`.
    _EDITABLE = frozenset({"extraction_method", "name", "category", "poll_interval", "active"})

    def _apply_source_update(self, query, params):
        """Apply an edit-route UPDATE, or return ``NotImplemented`` if this fake
        does not model the statement.

        Only claims statements it actually models: every assignment a ``%s``
        placeholder over an allowlisted column, plus the two WHERE params.
        Anything else — ``_record_poll_health``'s ``SET … = NULL`` clear (one
        param) or its three-param mark — falls through instead of being
        mis-parsed into the wrong row, which is how a fake starts making tests
        pass for the wrong reason.

        Returns the matched row id, or ``None`` when nothing matched (so the
        route's ``RETURNING id`` no-row path is reachable in tests).
        """
        q = self._norm(query)
        if not q.startswith("update sources set "):
            return NotImplemented
        assignments = q[len("update sources set ") :].split(" where ", 1)[0]
        columns = [part.split("=")[0].strip() for part in assignments.split(",")]
        values = list(params or ())
        if not set(columns).issubset(self._EDITABLE) or len(values) != len(columns) + 2:
            return NotImplemented
        sid, user_id = values[-2], values[-1]
        row = self.sources.get(sid)
        if not row or row["user_id"] != user_id:
            return None
        for column, value in zip(columns, values[: len(columns)]):
            row[column] = value
        return sid

    def execute(self, query, params=None):
        result = self._apply_source_update(query, params)
        if result is not NotImplemented:
            return
        return super().execute(query, params)

    def fetch_one(self, query, params=None):
        if "returning id" in self._norm(query):
            result = self._apply_source_update(query, params)
            if result is not NotImplemented:
                return None if result is None else {"id": result}
        return super().fetch_one(query, params)


@pytest.fixture
def env(monkeypatch):
    import signals.feed_monitor as fm

    import config as _config

    store = _ModeStore()
    sched = _FakeScheduler()
    _config._instances["metadata_store"] = store
    _config._instances["scheduler"] = sched
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("EMAIL_FULL_INGEST_ENABLED", raising=False)  # default off

    # The route imports schedule_source in-body, so a module-attr patch is picked
    # up per call. Capture the rescheduled SourceConfig (the fake scheduler drops args).
    calls: list = []
    monkeypatch.setattr(fm, "schedule_source", lambda src: calls.append(src))

    yield store, sched, calls
    _config._instances.pop("metadata_store", None)
    _config._instances.pop("scheduler", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def _create_source(client, *, full_ingest: bool = False) -> str:
    resp = client.post(
        "/me/email-sources",
        json={
            "host": "imap.example.com",
            "mailbox_label": "personal",
            "name": "Me",
            "full_ingest": full_ingest,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["source_id"]


# --- happy paths -----------------------------------------------------------


def test_toggle_metadata_to_full_ingest(env, monkeypatch):
    store, _sched, calls = env
    monkeypatch.setenv("EMAIL_FULL_INGEST_ENABLED", "true")
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "updated"
    assert body["extraction_method"] == "imap_full_ingest"
    assert body["full_ingest_active"] is True
    assert body["rescheduled"] is True
    assert store.sources[sid]["extraction_method"] == "imap_full_ingest"
    assert calls and calls[-1].extraction_method == "imap_full_ingest"


def test_toggle_full_ingest_to_metadata(env):
    store, _sched, calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=True)
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_method"] == "imap_metadata"
    assert body["full_ingest_active"] is False
    assert store.sources[sid]["extraction_method"] == "imap_metadata"
    assert calls and calls[-1].extraction_method == "imap_metadata"


def test_toggle_to_full_ingest_flag_off_is_stored_but_inactive(env, caplog):
    store, _sched, _calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        with caplog.at_level("WARNING"):
            resp = client.patch(f"/sources/{sid}", json={"full_ingest": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_method"] == "imap_full_ingest"  # intent stored
    assert body["full_ingest_active"] is False  # but inert (flag off)
    assert store.sources[sid]["extraction_method"] == "imap_full_ingest"
    assert any("requested_but_disabled" in r.getMessage() for r in caplog.records)


def test_get_sources_exposes_mode(env):
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        resp = client.get("/sources")
    assert resp.status_code == 200
    rows = resp.json()["sources"]
    row = next(r for r in rows if r["id"] == sid)
    assert row["extraction_method"] == "imap_metadata"
    assert row["full_ingest_active"] is False


def test_get_sources_exposes_healthy_default(env):
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        resp = client.get("/sources")
    row = next(r for r in resp.json()["sources"] if r["id"] == sid)
    assert row["last_poll_error_code"] is None and row["last_poll_error_at"] is None


def test_get_sources_exposes_poll_health(env):
    # LUM-690/633 — a mailbox feed_monitor marked unhealthy (couldn't
    # authenticate) is visible via GET /sources, not only in logs.
    import datetime

    store, _sched, _calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        store.sources[sid]["last_poll_error_code"] = "credential"
        store.sources[sid]["last_poll_error_at"] = datetime.datetime(
            2026,
            7,
            24,
            12,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        )
        resp = client.get("/sources")
    row = next(r for r in resp.json()["sources"] if r["id"] == sid)
    assert row["last_poll_error_code"] == "credential"
    assert row["last_poll_error_at"] == "2026-07-24T12:00:00+00:00"


def test_toggle_preserves_poll_cursor(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        store.sources[sid]["poll_cursor"] = "UID-42"
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": True})
    assert resp.status_code == 200
    assert store.sources[sid]["poll_cursor"] == "UID-42"  # untouched


def test_toggle_idempotent_noop(env):
    store, _sched, calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unchanged"
    assert body["rescheduled"] is False
    assert calls == []  # no reschedule on a no-op


def test_toggle_inactive_source_updates_but_does_not_reschedule(env):
    store, _sched, calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        uid = store.sources[sid]["user_id"]
        isid = "inactive-src"
        store.sources[isid] = {
            **store.sources[sid],
            "id": isid,
            "user_id": uid,
            "active": False,
            "extraction_method": "imap_metadata",
        }
        calls.clear()
        resp = client.patch(f"/sources/{isid}", json={"full_ingest": True})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert store.sources[isid]["extraction_method"] == "imap_full_ingest"  # DB updated
    assert calls == []  # not resurrected


def test_scheduler_failure_still_200(env, monkeypatch):
    store, _sched, _calls = env
    import signals.feed_monitor as fm

    def _boom(_src):
        raise RuntimeError("scheduler down")

    monkeypatch.setattr(fm, "schedule_source", _boom)
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": True})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert store.sources[sid]["extraction_method"] == "imap_full_ingest"  # DB still updated


def test_update_failure_returns_update_failed_without_leak(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        # LUM-837: the route's UPDATE now goes through `fetch_one` (it needs
        # `RETURNING id` to notice a row deleted mid-request), so the failure is
        # injected there. Same statement, same assertion.
        orig = store.fetch_one

        def _raise(query, params=None):
            if "update sources set extraction_method" in " ".join(query.split()).lower():
                raise RuntimeError("db down: postgres://secret@dbhost")
            return orig(query, params)

        store.fetch_one = _raise
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": True})
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "update_failed"
    assert "secret" not in resp.text  # raw driver exc not echoed to the client


# --- negative / authz ------------------------------------------------------


def test_foreign_source_returns_404(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        fsid = "foreign-src"
        store.sources[fsid] = {
            **store.sources[sid],
            "id": fsid,
            "user_id": "someone-else",
            "extraction_method": "imap_metadata",
        }
        resp = client.patch(f"/sources/{fsid}", json={"full_ingest": True})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"
    assert store.sources[fsid]["extraction_method"] == "imap_metadata"  # not mutated


def test_unknown_source_returns_404(env):
    with _client() as client:
        _create_source(client, full_ingest=False)
        resp = client.patch("/sources/does-not-exist", json={"full_ingest": True})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"


def test_non_imap_source_returns_422(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        rsid = "rss-src"
        store.sources[rsid] = {
            **store.sources[sid],
            "id": rsid,
            "source_type": "rss",
            "extraction_method": "feedparser",
        }
        resp = client.patch(f"/sources/{rsid}", json={"full_ingest": True})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "mode_toggle_not_supported"
    assert store.sources[rsid]["extraction_method"] == "feedparser"  # not mutated


def test_missing_full_ingest_returns_422(env):
    with _client() as client:
        sid = _create_source(client, full_ingest=False)
        resp = client.patch(f"/sources/{sid}", json={})
    assert resp.status_code == 422
