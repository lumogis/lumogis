# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pause and resume a source (LUM-844).

``PATCH /sources/{id}`` accepts ``{"active": bool}``; the scheduler is made to
match the *committed* column by ``feed_monitor.reconcile_source_schedule`` under
one in-process lock, and ``POST /sources/{id}/sync`` refuses a paused row.

Unlike ``test_source_update.py`` this file does **not** replace
``schedule_source`` with a recorder: the real function runs against
``_FakeScheduler``, so ``sched.jobs`` is the observable state, and a spy records
the ``SourceConfig`` it was handed and then delegates. That proves both "the job
exists" and "it was built from this row".

Seed rows **inside** ``with _client()`` — lifespan ``start()`` schedules every
active row it finds, which would replace a job planted beforehand.
"""

from __future__ import annotations

import dataclasses
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from tests.test_email_source_routes import _FakeScheduler
from tests.test_source_mode_toggle import _ModeStore
from tests.test_source_update import _seed


class _PauseStore(_ModeStore):
    """``fetch_all`` that applies ``active = TRUE`` only when the SQL asks for it.

    The shared ``_EmailRoutesStore.fetch_all`` returns only active rows for any
    ``FROM sources`` query whatever the SQL says, which would make the list test
    and the startup test unable to fail. Kept local so ``test_source_update`` and
    ``test_source_mode_toggle`` (which share ``_ModeStore``) are untouched.
    """

    def fetch_all(self, query, params=None):
        q = self._norm(query)
        if "from sources" not in q:
            return super().fetch_all(query, params)
        rows = list(self.sources.values())
        if "active = true" in q:
            rows = [r for r in rows if r.get("active")]
        if params:
            rows = [r for r in rows if r["user_id"] == params[0]]
        return [dict(r) for r in rows]


def _job(job_id: str):
    return type("Job", (), {"id": job_id, "remove": lambda self: None})()


@pytest.fixture
def env(monkeypatch):
    import signals.feed_monitor as fm

    import config as _config

    store = _PauseStore()
    sched = _FakeScheduler()
    _config._instances["metadata_store"] = store
    _config._instances["scheduler"] = sched
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("EMAIL_FULL_INGEST_ENABLED", raising=False)

    spy: list = []
    real = fm.schedule_source

    def _spy(src):
        spy.append(src)
        return real(src)

    monkeypatch.setattr(fm, "schedule_source", _spy)
    yield SimpleNamespace(store=store, sched=sched, spy=spy, fm=fm)
    _config._instances.pop("metadata_store", None)
    _config._instances.pop("scheduler", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def _seed_scheduled(env, sid: str, **kw) -> str:
    """Seed an active row and give it a poll job, as `start()` would have."""
    _seed(env.store, sid, **kw)
    env.sched.jobs[f"signal_poll_{sid}"] = _job(f"signal_poll_{sid}")
    env.spy.clear()
    return sid


def _norm(q) -> str:
    return " ".join(str(q).lower().split())


_TYPES = [
    ("paperless", "paperless_http"),
    ("rss", "feedparser"),
    ("page", "trafilatura"),
]


# --- pause -------------------------------------------------------------------


@pytest.mark.parametrize("source_type,method", _TYPES)
def test_pausing_removes_the_poll_job(env, source_type, method):
    with _client() as client:
        sid = _seed_scheduled(
            env, "s1", source_type=source_type, extraction_method=method, poll_cursor="cursor-123"
        )
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["active"] is False
    assert body["rescheduled"] is False
    assert body["schedule_synced"] is True
    assert f"signal_poll_{sid}" not in env.sched.jobs
    assert env.store.sources[sid]["active"] is False
    # Proves only that pause does not WRITE the cursor — for rss/page no poller
    # reads it, so this is not evidence about resume semantics.
    assert env.store.sources[sid]["poll_cursor"] == "cursor-123"
    assert env.spy == []


def test_a_paused_source_lists_as_not_polling(env):
    with _client() as client:
        sid = _seed_scheduled(
            env,
            "s1",
            source_type="rss",
            extraction_method="feedparser",
            # the list route reads these columns unconditionally
            last_polled_at=None,
            last_signal_at=None,
            last_poll_error_at=None,
            last_poll_error_code=None,
            attachment_ingest_since=None,
        )
        assert client.patch(f"/sources/{sid}", json={"active": False}).status_code == 200
        resp = client.get("/sources")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json()["sources"] if r["id"] == sid]
    assert len(rows) == 1
    assert rows[0]["active"] is False
    assert rows[0]["polling_active"] is False


def test_resuming_schedules_a_fresh_job_from_the_stored_row(env):
    with _client() as client:
        sid = "s1"
        _seed(env.store, sid, active=False, poll_cursor="cursor-123", poll_interval=3600)
        r1 = client.patch(f"/sources/{sid}", json={"poll_interval": 900})
        assert r1.status_code == 200
        assert f"signal_sync_now_{sid}" not in env.sched.jobs  # an edit is not a resume
        env.spy.clear()
        r2 = client.patch(f"/sources/{sid}", json={"active": True})
    assert r2.status_code == 200, r2.text
    assert r2.json()["rescheduled"] is True
    assert f"signal_poll_{sid}" in env.sched.jobs
    cfg = env.spy[-1]
    assert cfg.poll_cursor == "cursor-123"
    assert cfg.poll_interval == 900
    assert cfg.active is True
    assert env.store.sources[sid]["poll_cursor"] == "cursor-123"
    # The immediate check on resume. **Mutation target:** drop `run_now`.
    assert f"signal_sync_now_{sid}" in env.sched.jobs


def test_pause_and_interval_in_one_body_store_both_and_leave_no_job(env):
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        resp = client.patch(f"/sources/{sid}", json={"active": False, "poll_interval": 900})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert env.store.sources[sid]["active"] is False
    assert env.store.sources[sid]["poll_interval"] == 900
    assert f"signal_poll_{sid}" not in env.sched.jobs


def test_active_alone_is_a_valid_body(env):
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    assert resp.status_code != 422


def test_active_null_alone_is_422(env):
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        resp = client.patch(f"/sources/{sid}", json={"active": None})
    assert resp.status_code == 422
    assert env.store.sources[sid]["active"] is True


# --- re-sending repairs the schedule ----------------------------------------


def test_re_sending_pause_repairs_a_job_left_on_a_paused_row(env):
    """The state a failed pause reconcile leaves behind. **Mutation target:** skip
    the reconcile on the ``unchanged`` path."""
    updates: list = []
    with _client() as client:
        sid = "s1"
        _seed(env.store, sid, active=False)
        env.sched.jobs[f"signal_poll_{sid}"] = _job(f"signal_poll_{sid}")
        original = env.store.fetch_one

        def _spy(query, params=None):
            if _norm(query).startswith("update sources set"):
                updates.append(query)
            return original(query, params)

        env.store.fetch_one = _spy  # type: ignore[method-assign]
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    body = resp.json()
    assert body["status"] == "unchanged"
    assert body["schedule_synced"] is True
    assert body["rescheduled"] is False
    assert f"signal_poll_{sid}" not in env.sched.jobs
    assert env.spy == []
    assert updates == []


def test_re_sending_resume_repairs_a_missing_job(env):
    with _client() as client:
        sid = "s1"
        _seed(env.store, sid, active=True)
        env.sched.jobs.pop(f"signal_poll_{sid}", None)
        env.sched.jobs.pop(f"signal_sync_now_{sid}", None)
        resp = client.patch(f"/sources/{sid}", json={"active": True})
    body = resp.json()
    assert body["status"] == "unchanged"
    assert body["rescheduled"] is True
    assert body["schedule_synced"] is True
    assert f"signal_poll_{sid}" in env.sched.jobs
    assert f"signal_sync_now_{sid}" not in env.sched.jobs  # a repair is not a resume


# --- the two race orderings --------------------------------------------------


def test_an_edit_that_lost_a_race_to_a_pause_does_not_bring_the_job_back(env):
    """Edit's UPDATE commits, a concurrent pause then flips the column and removes
    the job, and the edit goes on to reschedule. Reconcile must re-read the column,
    not trust the pre-update row or the UPDATE's own RETURNING."""
    store, sched = env.store, env.sched
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        original = store.fetch_one

        def _race(query, params=None):
            result = original(query, params)
            q = _norm(query)
            if q.startswith("update sources set") and "returning id" in q:
                store.sources[sid]["active"] = False
                sched.jobs.pop(f"signal_poll_{sid}", None)
            return result

        store.fetch_one = _race  # type: ignore[method-assign]
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 900})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert f"signal_poll_{sid}" not in sched.jobs
    assert env.spy == []


def test_an_edit_whose_select_saw_active_but_whose_update_ran_after_a_pause_does_not_reschedule(
    env,
):
    store, sched = env.store, env.sched
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        original = store.fetch_one
        flipped = []

        def _racing(query, params=None):
            result = original(query, params)
            if not flipped and _norm(query).startswith("select id, name, source_type"):
                flipped.append(True)
                store.sources[sid]["active"] = False
                sched.jobs.pop(f"signal_poll_{sid}", None)
            return result

        store.fetch_one = _racing  # type: ignore[method-assign]
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 900})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert f"signal_poll_{sid}" not in sched.jobs
    assert env.spy == []


# --- authz and refusals -------------------------------------------------------


def test_pausing_a_foreign_source_is_404_and_calls_no_scheduler(env):
    with _client() as client:
        sid = "s-foreign"
        _seed(env.store, sid, user_id="someone-else")
        env.sched.jobs[f"signal_poll_{sid}"] = _job(f"signal_poll_{sid}")
        sentinel = env.sched.jobs[f"signal_poll_{sid}"]
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"
    assert env.store.sources[sid]["active"] is True
    assert env.sched.jobs[f"signal_poll_{sid}"] is sentinel
    assert env.spy == []


@pytest.mark.parametrize("value", [False, True])
def test_pausing_a_mailbox_is_refused(env, value):
    updates: list = []
    with _client() as client:
        sid = "mbx"
        _seed(env.store, sid, source_type="imap", extraction_method="imap_metadata")
        original = env.store.fetch_one

        def _spy(query, params=None):
            if _norm(query).startswith("update sources set"):
                updates.append(query)
            return original(query, params)

        env.store.fetch_one = _spy  # type: ignore[method-assign]
        resp = client.patch(f"/sources/{sid}", json={"active": value})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "pause_not_supported"
    assert env.store.sources[sid]["active"] is True
    assert updates == []


# --- Sync now ------------------------------------------------------------------


def test_sync_now_on_a_paused_source_is_409_and_enqueues_nothing(env):
    with _client() as client:
        sid = "s1"
        _seed(env.store, sid, active=False)
        resp = client.post(f"/sources/{sid}/sync")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "source_paused"
    assert f"signal_sync_now_{sid}" not in env.sched.jobs


def test_sync_now_on_a_foreign_paused_source_is_404_not_409(env):
    """No existence oracle: ownership is checked before the paused state."""
    with _client() as client:
        sid = "s-foreign"
        _seed(env.store, sid, active=False, user_id="someone-else")
        resp = client.post(f"/sources/{sid}/sync")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"


def test_sync_now_reports_paused_before_already_running(env):
    with _client() as client:
        sid = "s1"
        _seed(env.store, sid, active=False)
        env.sched.jobs[f"signal_sync_now_{sid}"] = _job(f"signal_sync_now_{sid}")
        resp = client.post(f"/sources/{sid}/sync")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "source_paused"


def test_sync_now_on_an_active_source_still_queues(env):
    with _client() as client:
        sid = "s1"
        _seed(env.store, sid)
        resp = client.post(f"/sources/{sid}/sync")
    assert resp.status_code == 202
    assert f"signal_sync_now_{sid}" in env.sched.jobs


def test_pausing_removes_a_pending_sync_now_job(env):
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        env.sched.jobs[f"signal_sync_now_{sid}"] = _job(f"signal_sync_now_{sid}")
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    assert resp.status_code == 200
    assert f"signal_sync_now_{sid}" not in env.sched.jobs


def test_a_sync_now_job_that_fired_mid_pause_does_not_fail_the_pause(env, caplog):
    """APScheduler removes a run-once job from its own thread once it fires, so
    ``remove_job`` can raise ``JobLookupError`` (a ``KeyError``) mid-pause. The
    poll job must be removed first, so that raising never leaves it behind."""
    import config as _config

    class _RacySched(_FakeScheduler):
        def remove_job(self, job_id, *a, **k):
            if job_id.startswith("signal_sync_now_"):
                self.jobs.pop(job_id, None)
                raise KeyError(job_id)
            return super().remove_job(job_id, *a, **k)

    racy = _RacySched()
    _config._instances["scheduler"] = racy
    env.sched = racy
    with caplog.at_level("WARNING"), _client() as client:
        sid = _seed_scheduled(env, "s1", source_type="rss", extraction_method="feedparser")
        racy.jobs[f"signal_sync_now_{sid}"] = _job(f"signal_sync_now_{sid}")
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rescheduled"] is False
    assert body["schedule_synced"] is True
    assert f"signal_poll_{sid}" not in racy.jobs
    assert not any("Could not reconcile" in r.getMessage() for r in caplog.records)


# --- reconcile failures ---------------------------------------------------------


def test_a_failed_pause_reconcile_still_removes_the_jobs_and_reports_unsynced(env, caplog):
    """The reconcile SELECT raising (an exhausted pool, say) must not leave a
    paused row polling: ``stop_on_error`` removes both jobs before re-raising."""
    store, sched = env.store, env.sched
    with caplog.at_level("WARNING"), _client() as client:
        sid = _seed_scheduled(env, "s1")
        sched.jobs[f"signal_sync_now_{sid}"] = _job(f"signal_sync_now_{sid}")
        original = store.fetch_one
        selects: list = []

        def _flaky(query, params=None):
            if _norm(query).startswith("select id, name, source_type, url, category"):
                selects.append(1)
                if len(selects) == 2:  # the first is PATCH's own read
                    raise RuntimeError("pool exhausted")
            return original(query, params)

        store.fetch_one = _flaky  # type: ignore[method-assign]
        resp = client.patch(f"/sources/{sid}", json={"active": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["schedule_synced"] is False
    assert body["rescheduled"] is False
    assert body["active"] is False  # the request value: reconcile read nothing
    assert store.sources[sid]["active"] is False
    assert f"signal_poll_{sid}" not in sched.jobs
    assert f"signal_sync_now_{sid}" not in sched.jobs
    assert len([r for r in caplog.records if "Could not reconcile" in r.getMessage()]) == 1


def test_a_failed_resume_reconcile_reports_unsynced(env, monkeypatch, caplog):
    fm = env.fm

    def _boom(src):
        raise RuntimeError("scheduler down")

    monkeypatch.setattr(fm, "schedule_source", _boom)
    with caplog.at_level("WARNING"), _client() as client:
        sid = "s1"
        _seed(env.store, sid, active=False)
        resp = client.patch(f"/sources/{sid}", json={"active": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["schedule_synced"] is False
    assert body["rescheduled"] is False
    assert env.store.sources[sid]["active"] is True
    assert len([r for r in caplog.records if "Could not reconcile" in r.getMessage()]) == 1


def test_a_failed_sync_enqueue_is_500_and_releases_the_lock(env):
    class _BrokenAdd(_FakeScheduler):
        def add_job(self, func, *a, **kwargs):
            if str(kwargs.get("id", "")).startswith("signal_sync_now_"):
                raise RuntimeError("scheduler down")
            return super().add_job(func, *a, **kwargs)

    import config as _config

    broken = _BrokenAdd()
    _config._instances["scheduler"] = broken
    with _client() as client:
        sid = _seed_scheduled(env, "s1")
        broken.jobs[f"signal_poll_{sid}"] = _job(f"signal_poll_{sid}")
        resp = client.post(f"/sources/{sid}/sync")
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "enqueue_failed"
        assert env.fm._source_schedule_lock.acquire(blocking=False) is True
        env.fm._source_schedule_lock.release()
        assert client.patch(f"/sources/{sid}", json={"active": False}).status_code == 200


# --- the lock -------------------------------------------------------------------


def test_reconcile_and_sync_now_exclude_each_other(env):
    """Two real threads, no TestClient. A is inside ``enqueue_sync_now``'s SELECT
    (so holds the lock) while B tries to reconcile; B's SELECT must not run until
    A lets go. **Mutation target:** replace the lock with ``nullcontext`` in
    either helper."""
    store = env.store
    _seed(store, "s1")
    uid = store.sources["s1"]["user_id"]
    original = store.fetch_one
    a_in_select, release, b_selected = threading.Event(), threading.Event(), threading.Event()
    seen: list = []

    def _blocking(query, params=None):
        if _norm(query).startswith("select id, name, source_type, url, category, active"):
            seen.append(1)
            if len(seen) == 1:
                a_in_select.set()
                release.wait(5)
            else:
                b_selected.set()
        return original(query, params)

    store.fetch_one = _blocking  # type: ignore[method-assign]
    errors: list = []

    def _run(fn):
        try:
            fn()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    ta = threading.Thread(target=_run, args=(lambda: env.fm.enqueue_sync_now("s1", uid),))
    tb = threading.Thread(target=_run, args=(lambda: env.fm.reconcile_source_schedule("s1", uid),))
    ta.start()
    assert a_in_select.wait(5)
    tb.start()
    assert b_selected.wait(0.3) is False, "reconcile ran its SELECT while Sync now held the lock"
    release.set()
    ta.join(5)
    tb.join(5)
    assert b_selected.is_set()
    assert errors == []


# --- restart, page sweep, column list --------------------------------------------


def test_startup_schedules_no_job_for_a_paused_source(env):
    fm = env.fm
    _seed(env.store, "live", source_type="rss", extraction_method="feedparser")
    _seed(env.store, "paused", source_type="rss", extraction_method="feedparser", active=False)
    captured: list = []
    original = env.store.fetch_all

    def _capture(query, params=None):
        captured.append(query)
        return original(query, params)

    env.store.fetch_all = _capture  # type: ignore[method-assign]
    fm.start()
    assert "active = TRUE" in captured[0]
    assert "signal_poll_live" in env.sched.jobs
    assert "signal_poll_paused" not in env.sched.jobs


def test_the_page_monitor_skips_paused_sources(env):
    """A structural pin: the sweep does network work on its rows, so the fake
    returns none and only the SQL is asserted."""
    from signals import page_monitor

    captured: list = []
    env.store.fetch_all = lambda query, params=None: captured.append(query) or []  # type: ignore[method-assign]
    page_monitor._poll_all_pages()
    assert captured and "active = TRUE" in captured[0]


def test_every_SourceConfig_field_is_in_the_poll_column_list(env):
    """A dropped ``user_id`` would be silently defaulted to ``"default"`` by
    ``_row_to_source``, which the prefix-matching fake cannot see."""
    from models.signals import SourceConfig

    fm = env.fm
    fields = {f.name for f in dataclasses.fields(SourceConfig)}
    assert fields == {c.strip() for c in fm._SOURCE_POLL_COLUMNS.split(",")}
    captured: list = []
    env.store.fetch_all = lambda query, params=None: captured.append(query) or []  # type: ignore[method-assign]
    fm.start()
    assert fm._SOURCE_POLL_COLUMNS in captured[0]


def test_an_interval_edit_on_a_mailbox_reschedules_from_the_full_row(env):
    """The one IMAP-visible change: the reschedule snapshot now matches what
    ``start()`` builds at boot (the old PATCH snapshot had ``last_polled_at=None``)."""
    from datetime import datetime
    from datetime import timezone

    polled = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    with _client() as client:
        sid = _seed_scheduled(
            env,
            "mbx",
            source_type="imap",
            extraction_method="imap_metadata",
            last_polled_at=polled,
            poll_cursor="7:1234",
        )
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 900})
    assert resp.status_code == 200
    cfg = env.spy[-1]
    assert cfg.user_id == env.store.sources[sid]["user_id"]
    assert cfg.extraction_method == "imap_metadata"
    assert cfg.poll_interval == 900
    assert cfg.last_polled_at == polled
    assert cfg.poll_cursor == "7:1234"


def test_a_reconcile_that_failed_after_reading_an_active_row_keeps_its_job(env, monkeypatch):
    """``stop_on_error`` is fail-closed for an *unknown* state (the re-read failed),
    but when the row was read as active a later scheduler failure must leave the
    old job alone — it must not kill a source a concurrent resume just started."""
    fm = env.fm
    with _client() as client:
        sid = _seed_scheduled(env, "s1")

        def _boom(src):
            raise RuntimeError("scheduler down")

        monkeypatch.setattr(fm, "schedule_source", _boom)
        with pytest.raises(RuntimeError):
            fm.reconcile_source_schedule(sid, env.store.sources[sid]["user_id"], stop_on_error=True)
        assert f"signal_poll_{sid}" in env.sched.jobs
        del client
