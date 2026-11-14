# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for the widened PATCH /sources/{id} — general source edit (LUM-837).

``test_source_mode_toggle.py`` keeps everything about ``full_ingest``; this file
holds the general-field cases (name, category, poll interval, the column
allowlist, the paused-source guard, the 404). Both share ``_ModeStore`` — one
fake for one route.

The delete-path fix LUM-837 shipped alongside the widening (non-IMAP sources must
be unscheduled on delete) is tested in ``test_source_delete.py``, which already
owns that route's fake and its scheduler assertions — one fake per route.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_email_source_routes import _FakeScheduler
from tests.test_source_mode_toggle import _ModeStore


@pytest.fixture
def env(monkeypatch):
    import config as _config

    store = _ModeStore()
    sched = _FakeScheduler()
    _config._instances["metadata_store"] = store
    _config._instances["scheduler"] = sched
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("EMAIL_FULL_INGEST_ENABLED", raising=False)

    import signals.feed_monitor as fm

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


def _seed(store, source_id: str, *, source_type: str = "paperless", **overrides) -> str:
    """Put a non-IMAP source straight into the fake store.

    ``POST /sources`` is admin-gated and runs detection; these tests are about
    the edit route, so the row is seeded rather than created through it.
    """
    from auth import UserContext

    row = {
        "id": source_id,
        # Under AUTH_ENABLED=false the caller is the default context — read it
        # from the type rather than repeating the literal, so a change to the
        # dev user id does not silently turn these into 404 tests.
        "user_id": UserContext().user_id,
        "name": "Archive",
        "source_type": source_type,
        "url": "https://paperless.example.com",
        "category": "general",
        "active": True,
        "poll_interval": 3600,
        "extraction_method": "paperless_http",
        "css_selector_override": None,
        "poll_cursor": None,
    }
    row.update(overrides)
    store.sources[source_id] = row
    return source_id


# --- the gap D5 found: nothing could edit a non-IMAP source ----------------


def test_name_category_interval_are_editable_on_a_paperless_source(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(
            f"/sources/{sid}",
            json={"name": "Household archive", "category": "admin", "poll_interval": 900},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["name"] == "Household archive"
    assert body["category"] == "admin"
    assert body["poll_interval"] == 900
    assert store.sources[sid]["name"] == "Household archive"
    assert store.sources[sid]["category"] == "admin"
    assert store.sources[sid]["poll_interval"] == 900


def test_name_is_editable_on_an_rss_source(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-rss", source_type="rss", extraction_method="feedparser")
        resp = client.patch(f"/sources/{sid}", json={"name": "Weather"})
    assert resp.status_code == 200
    assert store.sources[sid]["name"] == "Weather"


# --- the narrowing must not widen ------------------------------------------


def test_full_ingest_still_422s_on_a_non_imap_source(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": True})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "mode_toggle_not_supported"
    assert store.sources[sid]["extraction_method"] == "paperless_http"


def test_a_general_field_alongside_full_ingest_is_still_422_on_non_imap(env):
    """The refusal is about `full_ingest`, and supplying a valid field with it
    must not smuggle the request past the guard — nor half-apply it."""
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"full_ingest": True, "name": "Nope"})
    assert resp.status_code == 422
    assert store.sources[sid]["name"] == "Archive"  # not partially applied


# --- the contract an earlier draft of the plan proposed to invert -----------


def test_an_empty_body_is_still_422(env):
    """`unchanged` means "the values you supplied already match", never
    "you supplied nothing". Pairs with the shipped
    `test_source_mode_toggle.py::test_missing_full_ingest_returns_422`."""
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={})
    assert resp.status_code == 422


def test_unchanged_is_returned_when_supplied_values_already_match(env):
    store, _sched, calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless", name="Archive", category="general")
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"name": "Archive", "category": "general"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unchanged"
    assert body["rescheduled"] is False
    assert body["name"] == "Archive"  # still echoed
    assert calls == []  # nothing to reschedule


# --- bounds -----------------------------------------------------------------


def test_poll_interval_below_the_floor_is_refused(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 5})
    assert resp.status_code == 422
    assert store.sources[sid]["poll_interval"] == 3600


def test_a_blank_name_is_refused(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"name": ""})
    assert resp.status_code == 422
    assert store.sources[sid]["name"] == "Archive"


def test_a_blank_category_is_refused(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"category": ""})
    assert resp.status_code == 422


# --- rescheduling -----------------------------------------------------------


def test_changing_poll_interval_reschedules(env):
    """The scheduled job holds a `SourceConfig` snapshot, so the new interval
    only takes effect if the job is replaced."""
    store, _sched, calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 600})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is True
    assert calls and calls[-1].poll_interval == 600


def test_renaming_alone_does_not_reschedule(env):
    """A name is not in the job's snapshot, so replacing the job would be churn."""
    store, _sched, calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert calls == []


def test_a_paused_source_is_not_resurrected_by_an_edit(env):
    store, _sched, calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless", active=False)
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 600})
    assert resp.status_code == 200
    assert resp.json()["rescheduled"] is False
    assert calls == []
    assert store.sources[sid]["poll_interval"] == 600  # the edit still applied


# --- the response envelope --------------------------------------------------


def test_full_ingest_active_is_null_when_mode_was_not_supplied(env):
    """`false` already means "requested and inert because the env flag is off".
    A request that never mentioned mode must not assert anything about it."""
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["full_ingest_active"] is None


def test_only_supplied_fields_are_echoed(env):
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"name": "Renamed"})
    body = resp.json()
    assert body["name"] == "Renamed"
    assert "category" not in body
    assert "poll_interval" not in body


# --- isolation and the allowlist -------------------------------------------


def test_a_foreign_source_is_404_not_403(env):
    """No existence oracle: a source you do not own reads the same as one that
    does not exist."""
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-other", user_id="someone-else")
        resp = client.patch(f"/sources/{sid}", json={"name": "Mine now"})
    assert resp.status_code == 404
    assert store.sources[sid]["name"] == "Archive"


def test_url_is_not_editable(env):
    """Changing a URL is delete-and-recreate: the poll cursor, the detected type
    and the extraction method all derive from the original."""
    store, _sched, _calls = env
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(f"/sources/{sid}", json={"url": "https://evil.example.com"})
    # `url` is not a field on the model, so a body containing only it supplies
    # nothing the route accepts — and an empty update is a 422.
    assert resp.status_code == 422
    assert store.sources[sid]["url"] == "https://paperless.example.com"


def test_an_unknown_field_never_reaches_the_set_clause(env):
    """The SET clause is built from a fixed column allowlist, so no request key
    can become SQL text. Asserted rather than assumed.

    ``active`` was this test's example of a non-allowlisted key until LUM-844
    made it a real, allowlisted field; ``source_type`` and ``user_id`` are the
    columns that must stay unwritable.
    """
    store, _sched, _calls = env
    seen: list[str] = []
    original = store.fetch_one

    def _spy(query, params=None):
        seen.append(str(query))
        return original(query, params)

    store.fetch_one = _spy  # type: ignore[method-assign]
    with _client() as client:
        sid = _seed(store, "src-paperless")
        resp = client.patch(
            f"/sources/{sid}",
            json={"name": "Renamed", "source_type": "imap", "user_id": "root"},
        )
    assert resp.status_code == 200
    updates = [q for q in seen if "UPDATE sources SET" in q]
    assert updates, "expected an UPDATE"
    for query in updates:
        set_part = query.split("WHERE")[0]
        assert "source_type" not in set_part
        assert "user_id = %s" not in set_part
    assert store.sources[sid]["source_type"] == "paperless"  # untouched


# --- the PATCH/DELETE race --------------------------------------------------


def test_a_row_deleted_mid_request_is_404_and_reschedules_nothing(env):
    """PATCH's SELECT can win and its UPDATE lose: if a DELETE lands in between,
    the UPDATE matches no row. Without `RETURNING id` the route reported
    "updated" and then rescheduled a poll job for a row that no longer exists —
    re-creating the orphaned job that hoisting `unschedule_source` out of
    `delete_source`'s IMAP branch exists to prevent."""
    store, _sched, calls = env
    sid = "src-raced"

    original = store.fetch_one
    seen: list[str] = []

    def _racing(query, params=None):
        result = original(query, params)
        # After the route's initial SELECT resolves, drop the row — as a
        # concurrent DELETE would.
        if "select id, name, source_type" in " ".join(str(query).lower().split()):
            seen.append("selected")
            store.sources.pop(sid, None)
        return result

    store.fetch_one = _racing  # type: ignore[method-assign]

    with _client() as client:
        _seed(store, sid)
        calls.clear()
        resp = client.patch(f"/sources/{sid}", json={"poll_interval": 600})

    assert seen == ["selected"], "the SELECT must have run for this to be the race"
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"
    assert calls == [], "a deleted source must not be rescheduled"
