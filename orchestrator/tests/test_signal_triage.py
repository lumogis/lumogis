# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-643 — per-(user, signal) triage state: service + route + GET join.

Covers, through the public route/service interfaces (no private helpers):

* ``set_triage`` writes each status; ``snoozed`` requires a future, TZ-aware
  ``snoozed_until`` (else ``TriageValidationError``); non-snoozed nulls it; UPSERT
  is idempotent.
* ``PATCH/DELETE /signals/{id}/triage`` — happy path, 422 (bad status / bad
  snooze), 404 (not visible / FK race), cross-user isolation, DELETE idempotency,
  visibility-check-before-delete.
* ``GET /signals`` returns per-caller ``triage_status``/``snoozed_until`` via the
  LATERAL join; another member's triage never leaks; ``triaged=set`` returns the
  caller's full triaged set recency-independently and keeps the ``source_type=imap``
  filter (no non-email leak); its own limit cap applies.
* A fake-store exact-SQL/params PIN for the worst binding case (``scope=shared`` →
  ``visible_filter`` contributes ZERO params, so the LATERAL ``%s`` must still bind
  the caller — proving the prepend, not append).

The house fake store string-matches SQL and cannot execute it; a real-Postgres
companion test (``test_signal_triage_live.py``) executes the exact LATERAL against
the 057 schema to prove it is ``42702``-free and correctly bound.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from auth import UserContext
from fastapi import HTTPException
from routes.signals import TriageRequest
from routes.signals import clear_signal_triage
from routes.signals import list_signals
from routes.signals import set_signal_triage

import config
from services import signal_triage


def _future(minutes: int = 60) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


class TriageFakeStore:
    """In-memory store honouring the union + email + triaged filters and the UPSERT.

    Records the last ``fetch_all`` (query, params) so binding/limit pins can assert
    on the exact SQL the route ran.
    """

    def __init__(self):
        self.signals: list[dict] = []
        self.triage: dict[tuple[str, str], dict] = {}
        self._seq = 0
        self._triage_seq = 0
        self.last_query: str | None = None
        self.last_params: tuple | None = None
        self.raise_on_insert: Exception | None = None  # simulate an FK race

    # --- seeding -----------------------------------------------------------
    def add(self, *, user_id, scope, url, signal_id=None, created_at=None):
        self._seq += 1
        sid = signal_id or f"sig-{self._seq}"
        self.signals.append(
            {
                "signal_id": sid,
                "user_id": user_id,
                "source_id": f"src-{self._seq}",
                "title": f"Title {self._seq}",
                "url": url,
                "published_at": datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
                "content_summary": "",
                "entities": [],
                "topics": [],
                "importance_score": 0.5,
                "relevance_score": 0.5,
                "notified": False,
                "created_at": created_at
                or datetime(2026, 7, 20, 10, self._seq % 60, tzinfo=timezone.utc),
                "category": "",
                "from_addr": "",
                "from_display": "",
                "scope": scope,
                "source_url": None,
                "source_label": None,
                "published_from": None,
            }
        )
        return sid

    # --- MetadataStore port -----------------------------------------------
    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def _visible(self, me: str, row: dict) -> bool:
        return (row["scope"] == "personal" and row["user_id"] == me) or row["scope"] in (
            "shared",
            "system",
        )

    def fetch_one(self, query, params=None):
        q = query.lower()
        p = params or ()
        if "insert into signal_triage" in q:
            if self.raise_on_insert is not None:
                raise self.raise_on_insert
            user_id, signal_id, status, snoozed_until = p
            # Monotonic per-insert timestamp so triage_updated_at ordering + the
            # keyset cursor are deterministic (real NOW() would collide in-test).
            self._triage_seq += 1
            now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=self._triage_seq)
            row = {
                "signal_id": signal_id,
                "status": status,
                "snoozed_until": snoozed_until,
                "updated_at": now,
            }
            self.triage[(user_id, signal_id)] = {**row, "user_id": user_id}
            return {k: row[k] for k in ("signal_id", "status", "snoozed_until", "updated_at")}
        if "select 1 as ok from signals" in q:
            signal_id = p[0]
            me = p[1] if len(p) > 1 else None
            for r in self.signals:
                if str(r["signal_id"]) == str(signal_id) and self._visible(me, r):
                    return {"ok": 1}
            return None
        return None

    def execute(self, query, params=None):
        q = query.lower()
        p = params or ()
        if "delete from signal_triage" in q:
            user_id, signal_id = p
            self.triage.pop((user_id, str(signal_id)), None)

    def fetch_all(self, query, params=None):
        self.last_query = query
        self.last_params = tuple(params or ())
        q = query.lower()
        p = params or ()
        if "from signals" not in q:
            return []  # taxonomy/label lookups — no-op
        me = p[0]  # LATERAL caller id is the FIRST placeholder (prepended)
        limit = p[-1]
        rows = []
        for r in self.signals:
            if not self._visible(me, r):
                continue
            t = self.triage.get((me, str(r["signal_id"])))
            row = dict(r)
            row["triage_status"] = t["status"] if t else None
            row["snoozed_until"] = t["snoozed_until"] if t else None
            row["triage_updated_at"] = t["updated_at"] if t else None
            rows.append(row)
        if "url like" in q:
            rows = [r for r in rows if r["url"].startswith("imap://")]
        if "t.triage_status is not null" in q:
            rows = [r for r in rows if r["triage_status"] is not None]
            if "t.triage_updated_at <" in q:
                # Keyset cursor is bound just before the LIMIT placeholder.
                cursor = p[-2]
                if isinstance(cursor, str):
                    cursor = datetime.fromisoformat(cursor)
                rows = [r for r in rows if r["triage_updated_at"] < cursor]
            rows.sort(key=lambda r: r["triage_updated_at"], reverse=True)
        else:
            rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]


@pytest.fixture
def store():
    s = TriageFakeStore()
    config._instances["metadata_store"] = s
    config.invalidate_email_taxonomy_cache()
    yield s
    config._instances.pop("metadata_store", None)
    config.invalidate_email_taxonomy_cache()


def _viewer(user_id: str) -> UserContext:
    return UserContext(user_id=user_id, is_authenticated=True, role="user", allows_shared=True)


def _list_resp(
    user_id: str, *, source_type=None, triaged=None, triaged_before=None, scope=None, limit=50
) -> dict:
    return list_signals(
        topic=None,
        entity=None,
        source_id=None,
        source_type=source_type,
        min_relevance_score=None,
        scope=scope,
        triaged=triaged,
        triaged_before=triaged_before,
        limit=limit,
        user=_viewer(user_id),
    )


def _list(
    user_id: str, *, source_type=None, triaged=None, triaged_before=None, scope=None, limit=50
) -> list[dict]:
    return _list_resp(
        user_id,
        source_type=source_type,
        triaged=triaged,
        triaged_before=triaged_before,
        scope=scope,
        limit=limit,
    )["signals"]


# ---------------------------------------------------------------------------
# Service: set_triage / clear_triage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["needs_you", "good_to_know", "done"])
def test_set_triage_when_non_snoozed_then_writes_and_nulls_snoozed(store, status):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    row = signal_triage.set_triage("A", sid, status, None)
    assert row["status"] == status
    assert row["snoozed_until"] is None


def test_set_triage_when_snoozed_with_future_then_persists_until(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    until = _future(120)
    row = signal_triage.set_triage("A", sid, "snoozed", until)
    assert row["status"] == "snoozed"
    assert row["snoozed_until"] == until


def test_set_triage_when_snoozed_without_until_then_raises(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    with pytest.raises(signal_triage.TriageValidationError):
        signal_triage.set_triage("A", sid, "snoozed", None)


def test_set_triage_when_snoozed_until_in_past_then_raises(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    with pytest.raises(signal_triage.TriageValidationError):
        signal_triage.set_triage("A", sid, "snoozed", _future(-60))


def test_set_triage_when_snoozed_until_naive_then_raises(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    naive = datetime.now() + timedelta(hours=1)  # noqa: DTZ005 — deliberately naive
    with pytest.raises(signal_triage.TriageValidationError):
        signal_triage.set_triage("A", sid, "snoozed", naive)


def test_set_triage_when_bad_status_then_raises(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    with pytest.raises(signal_triage.TriageValidationError):
        signal_triage.set_triage("A", sid, "archived", None)


def test_set_triage_is_idempotent_upsert(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    signal_triage.set_triage("A", sid, "needs_you", None)
    signal_triage.set_triage("A", sid, "done", None)
    assert len(store.triage) == 1  # single PK row, overwritten
    assert store.triage[("A", sid)]["status"] == "done"


# ---------------------------------------------------------------------------
# Route: PATCH / DELETE
# ---------------------------------------------------------------------------


def test_patch_triage_when_visible_then_200_and_stored(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    resp = set_signal_triage(sid, TriageRequest(status="done"), user=_viewer("A"))
    assert resp["status"] == "done"
    assert resp["signal_id"] == str(sid)


def test_patch_triage_when_not_visible_then_404_and_no_write(store):
    sid = store.add(user_id="B", scope="personal", url="imap://b/1/1")  # Bob's private
    with pytest.raises(HTTPException) as exc:
        set_signal_triage(sid, TriageRequest(status="done"), user=_viewer("A"))
    assert exc.value.status_code == 404
    assert ("A", sid) not in store.triage  # nothing written


def test_patch_triage_when_bad_status_then_422(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    with pytest.raises(HTTPException) as exc:
        set_signal_triage(sid, TriageRequest(status="nope"), user=_viewer("A"))
    assert exc.value.status_code == 422


def test_patch_triage_when_snoozed_without_until_then_422(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    with pytest.raises(HTTPException) as exc:
        set_signal_triage(sid, TriageRequest(status="snoozed"), user=_viewer("A"))
    assert exc.value.status_code == 422


def test_delete_triage_when_visible_then_204_and_cleared(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    signal_triage.set_triage("A", sid, "done", None)
    resp = clear_signal_triage(sid, user=_viewer("A"))
    assert resp.status_code == 204
    assert ("A", sid) not in store.triage


def test_delete_triage_is_idempotent(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    resp = clear_signal_triage(sid, user=_viewer("A"))  # never triaged
    assert resp.status_code == 204


def test_delete_triage_when_not_visible_then_404(store):
    sid = store.add(user_id="B", scope="personal", url="imap://b/1/1")
    with pytest.raises(HTTPException) as exc:
        clear_signal_triage(sid, user=_viewer("A"))
    assert exc.value.status_code == 404


def test_patch_triage_when_fk_race_message_then_404(store):
    # Signal visible at guard time, but deleted before the UPSERT lands → the
    # store surfaces a (plain-Exception-wrapped) FK violation. Classified by
    # message → 404, not 500 (LUM-660).
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    store.raise_on_insert = Exception(
        'insert or update on table "signal_triage" violates foreign key '
        'constraint "signal_triage_signal_id_fkey"'
    )
    with pytest.raises(HTTPException) as exc:
        set_signal_triage(sid, TriageRequest(status="done"), user=_viewer("A"))
    assert exc.value.status_code == 404


def test_patch_triage_when_fk_race_typed_then_404(store):
    # Same race, but the store raises the typed psycopg2 ForeignKeyViolation —
    # classified by type even if the message were localized/reworded (LUM-660).
    pg_errors = pytest.importorskip("psycopg2.errors")
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    store.raise_on_insert = pg_errors.ForeignKeyViolation("localized fk text")
    with pytest.raises(HTTPException) as exc:
        set_signal_triage(sid, TriageRequest(status="done"), user=_viewer("A"))
    assert exc.value.status_code == 404


def test_patch_triage_when_non_fk_db_error_then_500(store):
    # A non-FK DB failure must NOT be misclassified as 404 — it surfaces as 500.
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    store.raise_on_insert = Exception("connection reset by peer")
    with pytest.raises(HTTPException) as exc:
        set_signal_triage(sid, TriageRequest(status="done"), user=_viewer("A"))
    assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# GET /signals: LATERAL join, isolation, triaged=set, source_type leak, limit
# ---------------------------------------------------------------------------


def test_get_signals_returns_caller_triage_only(store):
    sid = store.add(user_id="A", scope="personal", url="imap://a/1/1")
    signal_triage.set_triage("A", sid, "done", None)
    row = next(r for r in _list("A", source_type="imap") if r["signal_id"] == str(sid))
    assert row["triage_status"] == "done"
    assert row["snoozed_until"] is None


def test_get_signals_cross_user_isolation_shared_signal(store):
    # Bob shares an email; Alice AND Bob both see the projected row. Alice marks
    # it done. Bob's GET must NOT show Alice's triage.
    sid = store.add(user_id="B", scope="shared", url="imap://b/2/7", signal_id="shared-1")
    signal_triage.set_triage("A", sid, "done", None)
    alice_row = next(r for r in _list("A", source_type="imap") if r["signal_id"] == "shared-1")
    bob_row = next(r for r in _list("B", source_type="imap") if r["signal_id"] == "shared-1")
    assert alice_row["triage_status"] == "done"
    assert bob_row["triage_status"] is None  # default derivation, not Alice's state


def test_triaged_set_returns_item_older_than_recency_window(store):
    # An old done email + 5 newer untriaged ones; a recency window of 2 would drop
    # the old one, but triaged=set returns it regardless of age.
    old = store.add(
        user_id="A",
        scope="personal",
        url="imap://a/old/1",
        signal_id="old",
        created_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    for i in range(5):
        store.add(
            user_id="A",
            scope="personal",
            url=f"imap://a/new/{i}",
            created_at=datetime(2026, 7, i + 1, 0, 0, tzinfo=timezone.utc),
        )
    signal_triage.set_triage("A", old, "done", None)

    windowed = {r["signal_id"] for r in _list("A", source_type="imap", limit=2)}
    triaged = {r["signal_id"] for r in _list("A", source_type="imap", triaged="set", limit=2)}
    assert "old" not in windowed  # dropped by the recency window
    assert "old" in triaged  # recovered by the triaged=set fetch


def test_triaged_set_does_not_leak_non_email_triage(store):
    # Alice triages an RSS (non-email) signal done; an email-scoped triaged=set
    # fetch must NOT return it — the source_type=imap filter still applies.
    rss = store.add(user_id="A", scope="personal", url="https://news.test/x", signal_id="rss")
    mail = store.add(user_id="A", scope="personal", url="imap://a/1/1", signal_id="mail")
    signal_triage.set_triage("A", rss, "done", None)
    signal_triage.set_triage("A", mail, "done", None)
    ids = {r["signal_id"] for r in _list("A", source_type="imap", triaged="set")}
    assert ids == {"mail"}
    assert "rss" not in ids


def test_triaged_set_uses_its_own_limit_cap_not_base_le_100(store):
    store.add(user_id="A", scope="personal", url="imap://a/1/1")
    _list("A", source_type="imap", triaged="set", limit=100)
    # The last GET ran the triaged branch: its own cap (200), NOT the passed 100.
    assert store.last_params[-1] == 200


def test_base_fetch_uses_the_passed_limit(store):
    store.add(user_id="A", scope="personal", url="imap://a/1/1")
    _list("A", source_type="imap", limit=37)
    assert store.last_params[-1] == 37


def test_lateral_param_is_prepended_even_when_visible_filter_has_zero_params(store):
    # Worst binding case: scope='shared' → visible_filter contributes ZERO params.
    # The LATERAL's caller-id %s is the FIRST placeholder, so params must be
    # exactly (caller, limit) — proving the PREPEND (an append would misbind).
    store.add(user_id="B", scope="shared", url="imap://b/1/1")
    _list("A", scope="shared", limit=25)
    assert "left join lateral" in store.last_query.lower()
    assert "st.user_id = %s" in store.last_query.lower()
    assert store.last_params == ("A", 25)


# ---------------------------------------------------------------------------
# LUM-659: triaged-set keyset pagination (triaged_before cursor + next_before)
# ---------------------------------------------------------------------------


def test_triaged_before_returns_only_older_rows(store):
    # Triage three email signals in order (monotonic triage_updated_at). A cursor
    # at the middle item's timestamp returns only the strictly-older one.
    a = store.add(user_id="A", scope="personal", url="imap://a/1/1", signal_id="a")
    b = store.add(user_id="A", scope="personal", url="imap://a/2/2", signal_id="b")
    c = store.add(user_id="A", scope="personal", url="imap://a/3/3", signal_id="c")
    signal_triage.set_triage("A", a, "done", None)  # oldest triage
    signal_triage.set_triage("A", b, "done", None)
    signal_triage.set_triage("A", c, "done", None)  # newest triage
    # cursor = b's triage_updated_at → only 'a' (older) comes back.
    triaged_rows = _list("A", source_type="imap", triaged="set")
    b_ts = next(r for r in triaged_rows if r["signal_id"] == "b")["triage_updated_at"]
    older = {
        r["signal_id"] for r in _list("A", source_type="imap", triaged="set", triaged_before=b_ts)
    }
    assert older == {"a"}


def test_triaged_set_hands_back_next_before_when_page_full(store, monkeypatch):
    # With a page cap of 2 and three triaged items, page 1 returns 2 rows + a
    # next_before cursor; paging with it returns the last row and no cursor.
    monkeypatch.setattr("routes.signals._TRIAGED_SET_LIMIT", 2)
    for i in range(3):
        sid = store.add(user_id="A", scope="personal", url=f"imap://a/{i}/{i}", signal_id=f"s{i}")
        signal_triage.set_triage("A", sid, "done", None)

    page1 = _list_resp("A", source_type="imap", triaged="set")
    assert len(page1["signals"]) == 2
    assert page1["signals"][0]["signal_id"] == "s2"  # newest triage first
    assert page1.get("next_before") is not None

    page2 = _list_resp("A", source_type="imap", triaged="set", triaged_before=page1["next_before"])
    assert {r["signal_id"] for r in page2["signals"]} == {"s0"}
    assert page2.get("next_before") is None  # last page, no more


def test_triaged_before_ignored_without_triaged_set(store):
    # triaged_before only applies to triaged=set; the base window ignores it.
    store.add(user_id="A", scope="personal", url="imap://a/1/1", signal_id="a")
    rows = _list("A", source_type="imap", triaged_before="2020-01-01T00:00:00+00:00")
    assert {r["signal_id"] for r in rows} == {"a"}
