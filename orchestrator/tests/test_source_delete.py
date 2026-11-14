# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for DELETE /sources/{id} — remove a mailbox (LUM-701).

TestClient + fake metadata store + fake scheduler + spied
credential/cleanup/organize side effects — no live IMAP, DB, or APScheduler.
Reuses the LUM-170/LUM-628 email-route fake harness, extended with the
SELECT + DELETE...RETURNING shapes the new route issues.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_email_source_routes import _EmailRoutesStore
from tests.test_email_source_routes import _FakeScheduler

from services import connector_credentials as ccs


class _DeleteStore(_EmailRoutesStore):
    """Adds the delete-route's SELECT/DELETE shapes to the email-route fake store."""

    def execute(self, query, params=None):
        # LUM-844: delete pauses the row before its vector purge. Without this
        # branch the pre-delete pause would hit the base fake and the delete
        # tests would pass without ever pausing.
        q = self._norm(query)
        for stmt, value in (
            ("update sources set active = false where id::text = %s and user_id = %s", False),
            # the restore of a mailbox whose DELETE failed
            ("update sources set active = true where id::text = %s and user_id = %s", True),
        ):
            if q == stmt:
                sid, user_id = params
                row = self.sources.get(sid)
                if row and row["user_id"] == user_id:
                    row["active"] = value
                return None
        return super().execute(query, params)

    def fetch_one(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if q.startswith("select id::text as id, source_type from sources"):
            sid, user_id = p
            row = self.sources.get(sid)
            if row and row["user_id"] == user_id:
                return {"id": row["id"], "source_type": row["source_type"]}
            return None
        if q.startswith("delete from sources where id::text = %s and user_id = %s returning id"):
            sid, user_id = p
            row = self.sources.get(sid)
            if row and row["user_id"] == user_id:
                del self.sources[sid]
                return {"id": sid}
            return None
        return super().fetch_one(query, params)


@pytest.fixture
def env(monkeypatch):
    import config as _config

    store = _DeleteStore()
    sched = _FakeScheduler()
    _config._instances["metadata_store"] = store
    _config._instances["scheduler"] = sched
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)

    deletes: list = []
    monkeypatch.setattr(
        ccs, "delete_payload", lambda u, c, *, actor, instance="": deletes.append((c, instance))
    )

    from services import email_cleanup

    cancel_calls: list = []
    monkeypatch.setattr(
        email_cleanup,
        "cancel_active_jobs_for_source",
        lambda sid, uid: cancel_calls.append((sid, uid)) or 0,
    )

    from services import email_organize

    invalidate_calls: list = []
    monkeypatch.setattr(
        email_organize,
        "invalidate_opt_in_cache",
        lambda sid: invalidate_calls.append(sid),
    )

    yield store, sched, deletes, cancel_calls, invalidate_calls
    _config._instances.pop("metadata_store", None)
    _config._instances.pop("scheduler", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def _create_source(client) -> str:
    resp = client.post(
        "/me/email-sources",
        json={"host": "imap.example.com", "mailbox_label": "personal", "name": "Me"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["source_id"]


# --- happy path: IMAP mailbox -----------------------------------------------


def test_delete_imap_source_returns_200(env):
    store, _sched, _deletes, _cancel, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "deleted", "source_id": sid}
    assert sid not in store.sources


def test_delete_imap_source_unschedules_poll_job(env):
    store, sched, _deletes, _cancel, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        sched.jobs[f"signal_poll_{sid}"] = object()
        client.delete(f"/sources/{sid}")
    assert f"signal_poll_{sid}" not in sched.jobs


def test_delete_imap_source_deletes_both_credentials(env):
    store, _sched, deletes, _cancel, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        client.delete(f"/sources/{sid}")
    assert ("imap", sid) in deletes
    assert ("smtp", sid) in deletes


def test_delete_imap_source_cancels_active_cleanup_jobs(env):
    store, _sched, _deletes, cancel_calls, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        uid = store.sources[sid]["user_id"]
        client.delete(f"/sources/{sid}")
    assert cancel_calls == [(sid, uid)]


def test_delete_imap_source_invalidates_organize_opt_in_cache(env):
    store, _sched, _deletes, _cancel, invalidate_calls = env
    with _client() as client:
        sid = _create_source(client)
        client.delete(f"/sources/{sid}")
    assert invalidate_calls == [sid]


def test_delete_survives_cleanup_cancel_failure(env, monkeypatch):
    """A best-effort cleanup-job cancel failure must not block the delete."""
    store, _sched, _deletes, _cancel, _invalidate = env
    from services import email_cleanup

    def _boom(sid, uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(email_cleanup, "cancel_active_jobs_for_source", _boom)
    with _client() as client:
        sid = _create_source(client)
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert sid not in store.sources


def test_delete_survives_credential_cleanup_failure(env, monkeypatch):
    """A best-effort credential-delete failure must not surface as an error —
    the source row (what the user sees as "the mailbox") is already gone."""
    store, _sched, _deletes, _cancel, _invalidate = env

    def _boom(u, c, *, actor, instance=""):
        raise RuntimeError("vault unreachable")

    monkeypatch.setattr(ccs, "delete_payload", _boom)
    with _client() as client:
        sid = _create_source(client)
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert sid not in store.sources


# --- non-IMAP sources: no email-specific side effects -----------------------


def test_delete_non_imap_source_skips_email_side_effects(env):
    store, sched, deletes, cancel_calls, invalidate_calls = env
    with _client() as client:
        sid = _create_source(client)  # seed a user row via FakeUsersStore auth
        rsid = "rss-src"
        store.sources[rsid] = {**store.sources[sid], "id": rsid, "source_type": "rss"}
        resp = client.delete(f"/sources/{rsid}")
    assert resp.status_code == 200
    assert rsid not in store.sources
    assert deletes == []
    assert cancel_calls == []
    assert invalidate_calls == []


def test_delete_rss_source_unschedules_poll_job(env):
    """LUM-837. ``unschedule_source`` used to sit inside ``delete_source``'s
    ``if is_imap:`` branch — its only call site in the codebase — so deleting an
    RSS, page or paperless source removed the row and left an APScheduler job
    holding a ``SourceConfig`` snapshot, polling the deleted source's URL on its
    interval forever. Nothing else removed it.

    The test above asserted only that the *email* side effects are skipped for a
    non-IMAP row, which is why the gap survived: nobody asserted what a non-IMAP
    delete is supposed to *do*.
    """
    store, sched, _deletes, _cancel, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        rsid = "rss-src"
        store.sources[rsid] = {**store.sources[sid], "id": rsid, "source_type": "rss"}
        sched.jobs[f"signal_poll_{rsid}"] = object()
        client.delete(f"/sources/{rsid}")
    assert f"signal_poll_{rsid}" not in sched.jobs


def test_delete_paperless_source_unschedules_poll_job(env):
    store, sched, _deletes, _cancel, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        psid = "paperless-src"
        store.sources[psid] = {**store.sources[sid], "id": psid, "source_type": "paperless"}
        sched.jobs[f"signal_poll_{psid}"] = object()
        client.delete(f"/sources/{psid}")
    assert f"signal_poll_{psid}" not in sched.jobs


# --- negative / authz --------------------------------------------------------


def test_delete_foreign_source_returns_404(env):
    store, _sched, deletes, _cancel, _invalidate = env
    with _client() as client:
        sid = _create_source(client)
        fsid = "foreign-src"
        store.sources[fsid] = {**store.sources[sid], "id": fsid, "user_id": "someone-else"}
        resp = client.delete(f"/sources/{fsid}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"
    assert fsid in store.sources  # not mutated
    assert deletes == []


def test_delete_unknown_source_returns_404(env):
    with _client() as client:
        _create_source(client)
        resp = client.delete("/sources/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"


def test_delete_is_idempotent_second_call_404s(env):
    with _client() as client:
        sid = _create_source(client)
        first = client.delete(f"/sources/{sid}")
        second = client.delete(f"/sources/{sid}")
    assert first.status_code == 200
    assert second.status_code == 404
    assert second.json()["detail"]["code"] == "source_not_found"


# --- LUM-844: delete pauses the row, then reconciles around the purge --------


def _seed_rss(store, sid="rss-src"):
    """An active RSS row cloned from a freshly created mailbox's shape."""
    from auth import UserContext

    store.sources[sid] = {
        "id": sid,
        "user_id": UserContext().user_id,
        "name": "Weather",
        "source_type": "rss",
        "url": "https://example.com/feed",
        "category": "general",
        "active": True,
        "poll_interval": 3600,
        "extraction_method": "feedparser",
        "css_selector_override": None,
        "poll_cursor": None,
    }
    return sid


def test_a_job_scheduled_during_the_delete_is_removed_after_it(env):
    """A PATCH reconcile landing between delete's pre-unschedule and its DELETE
    plants a job for a row that is about to vanish. The post-delete reconcile
    (row gone -> unschedule) is what removes it. **Mutation target:** drop the
    post-delete reconcile and this fails."""
    store, sched, _deletes, _cancel, _invalidate = env
    sid = _seed_rss(store)
    original = store.fetch_one

    def _spy(query, params=None):
        if "delete from sources" in " ".join(str(query).lower().split()):
            sched.jobs[f"signal_poll_{sid}"] = object()
        return original(query, params)

    store.fetch_one = _spy  # type: ignore[method-assign]
    with _client() as client:
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert f"signal_poll_{sid}" not in sched.jobs


def test_delete_pauses_the_row_before_the_purge(env, monkeypatch):
    """With the slow vector purge running, nothing may poll or sync the row.
    **Mutation target:** drop the pre-delete ``UPDATE ... SET active = FALSE``."""
    import signals.feed_monitor as fm

    from services import document_purge

    store, sched, _deletes, _cancel, _invalidate = env
    seen: dict = {}

    def _purge(*, user_id, source_id):
        seen["active"] = store.sources[source_id]["active"]
        seen["poll_job"] = f"signal_poll_{source_id}" in sched.jobs
        seen["sync"] = fm.enqueue_sync_now(source_id, user_id)

    monkeypatch.setattr(document_purge, "purge_source_vectors", _purge)
    with _client() as client:
        # Seeded inside the client: lifespan `start()` schedules every active row.
        sid = _seed_rss(store)
        sched.jobs[f"signal_poll_{sid}"] = object()
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert seen == {"active": False, "poll_job": False, "sync": "paused"}


def test_a_post_delete_reconcile_failure_still_deletes(env, caplog):
    """The row is already gone when the post-delete reconcile runs; its failure
    must not turn a successful delete into an error."""
    store, _sched, _deletes, _cancel, _invalidate = env
    sid = _seed_rss(store)
    original = store.fetch_one

    def _spy(query, params=None):
        q = " ".join(str(query).lower().split())
        if q.startswith("select id, name, source_type") and sid not in store.sources:
            raise RuntimeError("pool exhausted")
        return original(query, params)

    store.fetch_one = _spy  # type: ignore[method-assign]
    with caplog.at_level("WARNING"), _client() as client:
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert any("delete_source: reconcile failed" in r.getMessage() for r in caplog.records)


def test_a_resume_during_the_purge_does_not_bring_polling_back(env, monkeypatch):
    """Delete pauses the row before the slow purge, but a PATCH ``active: true``
    (a second tab, or the Resume button on the row being deleted) landing during
    the purge would re-add a poll job and an immediate check that ingest into
    vectors the purge just removed. While a delete is in progress the source reads
    as paused whatever its column says. **Mutation target:** drop ``begin_delete``."""
    from services import document_purge

    store, sched, _deletes, _cancel, _invalidate = env
    seen: dict = {}

    def _purge(*, user_id, source_id):
        import signals.feed_monitor as fm

        fm.reconcile_source_schedule(source_id, user_id, run_now=True)  # the resume's reconcile
        seen["poll"] = f"signal_poll_{source_id}" in sched.jobs
        seen["sync_job"] = f"signal_sync_now_{source_id}" in sched.jobs
        seen["sync"] = fm.enqueue_sync_now(source_id, user_id)
        # as if the resume's UPDATE had flipped the column back
        store.sources[source_id]["active"] = True
        fm.reconcile_source_schedule(source_id, user_id, run_now=True)
        seen["poll_after_flip"] = f"signal_poll_{source_id}" in sched.jobs
        seen["sync_after_flip"] = fm.enqueue_sync_now(source_id, user_id)

    monkeypatch.setattr(document_purge, "purge_source_vectors", _purge)
    with _client() as client:
        sid = _seed_rss(store)
        resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert seen == {
        "poll": False,
        "sync_job": False,
        "sync": "paused",
        "poll_after_flip": False,
        "sync_after_flip": "paused",
    }
    import signals.feed_monitor as fm

    assert sid not in fm._deleting  # the mark never outlives the request


def test_a_failed_delete_puts_a_mailbox_back_to_active(env):
    """A paused non-IMAP row can be resumed from /me/sources; a paused MAILBOX
    cannot (PATCH refuses ``active`` on IMAP) and send, the From picker and
    backfills all filter on ``active``. So a DELETE that raises must restore it."""
    store, _sched, _deletes, _cancel, _invalidate = env
    original = store.fetch_one

    def _boom(query, params=None):
        if "delete from sources" in " ".join(str(query).lower().split()):
            raise RuntimeError("db down")
        return original(query, params)

    with _client() as client:
        sid = _create_source(client)
        store.fetch_one = _boom  # type: ignore[method-assign]
        try:
            resp = client.delete(f"/sources/{sid}")
        except RuntimeError:
            resp = None  # TestClient re-raises unhandled server errors
    assert resp is None or resp.status_code == 500
    assert store.sources[sid]["active"] is True
    import signals.feed_monitor as fm

    assert sid not in fm._deleting
