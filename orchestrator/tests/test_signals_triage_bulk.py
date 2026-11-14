# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Bulk triage — ``POST /signals/triage/bulk`` (LUM-696).

Three regressions are pinned here, all of which would ship green without a test
that names them:

* **The clear path's ``skipped`` arithmetic.** ``DELETE … RETURNING`` yields only
  ids that had a row to delete. Deriving ``skipped`` from it reports a signal
  that is visible but was never triaged — the ordinary state of most of an inbox
  — as "no longer available", contradicting ``clear_triage``'s documented
  idempotence. Both statements therefore return the **visible** set.

* **``ON CONFLICT DO UPDATE``, never ``DO NOTHING``.** A one-word slip is an easy
  thing to copy from another UPSERT and would drop every already-triaged id in a
  batch out of the result.

* **Visibility inside the write.** The single-signal path is check-then-write and
  races a concurrent delete (hence ``TriageSignalMissing``). The bulk path sources
  its rows from ``signals`` in the same statement, so a purge mid-flight cannot
  fail a batch of 200.

The house fake store string-matches SQL and cannot execute it, so ambiguity and
CTE-execution semantics are proven in
``tests/integration/test_signal_triage_live.py`` instead. What *this* file can
prove is the SQL's shape, its bound parameters, and the route's arithmetic.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from services import signal_triage

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

A = str(uuid.uuid4())
B = str(uuid.uuid4())
C = str(uuid.uuid4())
FOREIGN = str(uuid.uuid4())

_NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class _Store:
    """Records every statement; returns whatever ``visible`` was primed with."""

    def __init__(self, visible: list[str] | None = None, raise_on_fetch=None) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._visible = visible if visible is not None else []
        self._raise = raise_on_fetch
        # Which of the visible ids already had a triage row (drives the LEFT
        # JOIN's null side in the set path).
        self.pre_existing: set[str] = set()

    def fetch_all(self, sql, params=None):
        self.statements.append((sql, params or ()))
        if self._raise is not None:
            raise self._raise
        # Model Postgres's ACTUAL row semantics for the two CTEs, keyed on which
        # one the final SELECT reads. Without this the fake returns every visible
        # id whatever the statement says, and the round-1 P0 regression test
        # (`…_is_success_not_skipped`) becomes unfalsifiable — it would pass
        # against a statement that reads `FROM deleted`, which is precisely the
        # bug. Verified by mutation: flip the production SQL to `FROM deleted`
        # and that test must fail.
        tail = sql.rstrip()
        if tail.endswith("FROM deleted"):
            # DELETE … RETURNING yields ONLY ids that had a row to delete.
            return [{"signal_id": sid} for sid in self._visible if sid in self.pre_existing]
        if tail.endswith("FROM visible"):
            return [{"signal_id": sid} for sid in self._visible]
        # The SET statement: `visible LEFT JOIN upserted`. Every visible id comes
        # back; the join's null side would only appear under a `DO NOTHING` slip.
        return [
            {
                "signal_id": sid,
                "status": "done",
                "snoozed_until": None,
                "updated_at": _NOW,
            }
            for sid in self._visible
        ]

    def fetch_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        self.statements.append((sql, params or ()))
        return None

    def sql(self) -> str:
        assert self.statements, "no statement ran"
        return self.statements[-1][0]

    def params(self) -> tuple:
        assert self.statements, "no statement ran"
        return self.statements[-1][1]


@pytest.fixture
def store(monkeypatch):
    s = _Store(visible=[A, B, C])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


VISIBLE_CLAUSE = "((scope = 'personal' AND user_id = %s) OR scope IN ('shared','system'))"
VISIBLE_PARAMS = ("u1",)


def _client(monkeypatch, store_obj, *, user_id="u1"):
    """A TestClient over just the signals router, with auth stubbed."""
    from auth import UserContext
    from routes import signals as signals_routes

    monkeypatch.setattr(config, "get_metadata_store", lambda: store_obj)
    ctx = UserContext(user_id=user_id, role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(signals_routes.router)
    app.dependency_overrides[signals_routes.require_user] = lambda: ctx
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Service — the SET statement
# --------------------------------------------------------------------------- #


def test_bulk_set_writes_one_row_per_visible_signal(store):
    rows = signal_triage.set_triage_bulk(
        "u1",
        [A, B, C],
        "done",
        None,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )

    assert [r["signal_id"] for r in rows] == [A, B, C]
    assert len(store.statements) == 1, "a loop-of-singles implementation must fail here"


def test_a_batch_mixing_fresh_and_already_triaged_ids_returns_all_of_them(store):
    """``DO UPDATE``, never ``DO NOTHING`` (case 1b).

    ``DO NOTHING`` would drop every already-triaged id out of ``RETURNING``. The
    LEFT JOIN against ``visible`` means such an id still appears — with a NULL
    status, a visible wrong answer rather than a silent misclassification — but
    the statement must not be written that way in the first place.
    """
    store.pre_existing = {A}

    signal_triage.set_triage_bulk(
        "u1",
        [A, B],
        "done",
        None,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )

    sql = store.sql().upper()
    assert "ON CONFLICT (USER_ID, SIGNAL_ID) DO UPDATE" in sql
    assert "DO NOTHING" not in sql


def test_the_visible_set_not_the_write_result_drives_the_response(store):
    """Case 1c — the structural guard behind the round-1 P0."""
    signal_triage.set_triage_bulk(
        "u1",
        [A],
        "done",
        None,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )
    set_sql = store.sql()
    assert "FROM visible v LEFT JOIN upserted u" in set_sql

    signal_triage.clear_triage_bulk(
        "u1", [A], visible_clause=VISIBLE_CLAUSE, visible_params=VISIBLE_PARAMS
    )
    assert store.sql().rstrip().endswith("SELECT signal_id FROM visible")


def test_bulk_set_selects_rows_from_signals_so_the_fk_cannot_race(store):
    signal_triage.set_triage_bulk(
        "u1",
        [A],
        "done",
        None,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )

    sql = store.sql()
    assert "WITH visible AS (SELECT s.signal_id FROM signals s" in sql
    assert VISIBLE_CLAUSE in sql, "the visibility clause runs INSIDE the write"
    # A CTE cannot see the outer statement's tables — this is what makes the
    # unqualified `user_id`/`scope` in the clause unambiguous (42702).
    assert "USING signals" not in sql


def test_the_caller_user_id_is_bound_not_taken_from_anywhere_else(store):
    signal_triage.set_triage_bulk(
        "u1",
        [A],
        "done",
        None,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )
    # CTE binds first: (ids, *visible_params, user_id, status, snoozed_until)
    assert store.params() == ([A], "u1", "u1", "done", None)


def test_placeholder_order_puts_the_cte_first(store):
    signal_triage.clear_triage_bulk(
        "u1", [A, B], visible_clause=VISIBLE_CLAUSE, visible_params=VISIBLE_PARAMS
    )
    assert store.params() == ([A, B], "u1", "u1")


def test_duplicate_ids_in_one_request_are_deduplicated(store):
    signal_triage.set_triage_bulk(
        "u1",
        [A, A, B],
        "done",
        None,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )
    assert store.params()[0] == [A, B], "first-occurrence order, no duplicates"


def test_an_empty_id_list_never_touches_the_store(store):
    assert (
        signal_triage.set_triage_bulk(
            "u1",
            [],
            "done",
            None,
            visible_clause=VISIBLE_CLAUSE,
            visible_params=VISIBLE_PARAMS,
        )
        == []
    )
    assert store.statements == []


# --------------------------------------------------------------------------- #
# Service — validation happens ONCE for the batch
# --------------------------------------------------------------------------- #


def test_bulk_status_is_validated_once_not_per_row(store):
    with pytest.raises(signal_triage.TriageValidationError):
        signal_triage.set_triage_bulk(
            "u1",
            [A, B, C],
            "bogus",
            None,
            visible_clause=VISIBLE_CLAUSE,
            visible_params=VISIBLE_PARAMS,
        )
    assert store.statements == [], "no SQL may run for an invalid status"


@pytest.mark.parametrize(
    "snoozed_until",
    [
        None,
        datetime(2026, 7, 25, 12, 0),  # naive
        datetime(2020, 1, 1, tzinfo=timezone.utc),  # past
    ],
    ids=["missing", "naive", "past"],
)
def test_bulk_snooze_requires_a_future_tz_aware_timestamp(store, snoozed_until):
    with pytest.raises(signal_triage.TriageValidationError):
        signal_triage.set_triage_bulk(
            "u1",
            [A],
            "snoozed",
            snoozed_until,
            visible_clause=VISIBLE_CLAUSE,
            visible_params=VISIBLE_PARAMS,
        )
    assert store.statements == []


def test_non_snooze_status_nulls_snoozed_until(store):
    """The DB CHECK ``(status='snoozed') = (snoozed_until IS NOT NULL)`` would
    otherwise reject the row."""
    future = datetime.now(timezone.utc) + timedelta(days=1)

    signal_triage.set_triage_bulk(
        "u1",
        [A],
        "done",
        future,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )

    assert store.params()[-1] is None


def test_a_valid_future_snooze_is_bound_through(store):
    future = datetime.now(timezone.utc) + timedelta(hours=3)

    signal_triage.set_triage_bulk(
        "u1",
        [A],
        "snoozed",
        future,
        visible_clause=VISIBLE_CLAUSE,
        visible_params=VISIBLE_PARAMS,
    )

    assert store.params()[-1] == future


# --------------------------------------------------------------------------- #
# Service — the CLEAR statement
# --------------------------------------------------------------------------- #


def test_clear_runs_the_delete_inside_a_data_modifying_cte(store):
    signal_triage.clear_triage_bulk(
        "u1", [A], visible_clause=VISIBLE_CLAUSE, visible_params=VISIBLE_PARAMS
    )

    sql = store.sql()
    assert "deleted AS (DELETE FROM signal_triage" in sql
    assert "AND signal_id IN (SELECT signal_id FROM visible)" in sql
    # Postgres runs a data-modifying CTE exactly once and to completion whether
    # or not the outer query reads it — so the delete happens even though the
    # result set comes from `visible`.
    assert sql.rstrip().endswith("SELECT signal_id FROM visible")


# --------------------------------------------------------------------------- #
# Route — the `skipped` arithmetic
# --------------------------------------------------------------------------- #


def test_route_returns_updated_and_no_skipped_when_everything_is_visible(monkeypatch):
    s = _Store(visible=[A, B])
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [A, B], "status": "done"}
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert [u["signal_id"] for u in body["updated"]] == [A, B]
    assert body["skipped"] == []
    assert body["count"] == 2


def test_ids_the_caller_cannot_see_are_reported_as_skipped(monkeypatch):
    s = _Store(visible=[A])  # FOREIGN is not returned by the visible CTE
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [A, FOREIGN], "status": "done"}
    )

    body = r.json()
    assert body["skipped"] == [FOREIGN]
    assert body["count"] == 1


def test_clearing_a_visible_but_never_triaged_signal_is_success_not_skipped(monkeypatch):
    """The round-1 P0 (case 9b).

    ``DELETE … RETURNING`` returns nothing for a signal with no triage row. If
    ``skipped`` were derived from it, the ordinary state of most of an inbox
    would be reported as "no longer available" — a lie about a row sitting in
    front of the user, and a regression against ``clear_triage``'s idempotence
    (204 whether or not a row existed).
    """
    # B is visible and has NO triage row: the fake's clear branch returns every
    # visible id precisely because the real statement selects from `visible`.
    s = _Store(visible=[A, B])

    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [A, B], "status": None}
    )

    body = r.json()
    assert body["skipped"] == [], "a visible, untriaged signal is NOT skipped"
    assert {u["signal_id"] for u in body["updated"]} == {A, B}
    assert body["count"] == 2


def test_clear_reports_only_genuinely_invisible_ids_as_skipped(monkeypatch):
    s = _Store(visible=[B])  # B visible+untriaged; FOREIGN another member's
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [B, FOREIGN], "status": None}
    )

    body = r.json()
    assert body["skipped"] == [FOREIGN]
    assert [u["signal_id"] for u in body["updated"]] == [B]


def test_a_duplicate_requested_id_is_not_reported_skipped(monkeypatch):
    s = _Store(visible=[A])
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [A, A], "status": "done"}
    )

    body = r.json()
    assert body["skipped"] == [], "the second copy of A is not a missing signal"
    assert body["count"] == 1


# --------------------------------------------------------------------------- #
# Route — validation and failure
# --------------------------------------------------------------------------- #


def test_bad_status_is_422_and_no_sql_runs(monkeypatch):
    s = _Store(visible=[A])
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [A], "status": "bogus"}
    )

    assert r.status_code == 422
    assert s.statements == []


def test_over_the_cap_is_rejected_before_any_sql(monkeypatch):
    s = _Store(visible=[])
    ids = [str(uuid.uuid4()) for _ in range(signal_triage.MAX_BULK_SIGNALS + 1)]

    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": ids, "status": "done"}
    )

    assert r.status_code == 422
    assert s.statements == []


def test_an_empty_list_is_422(monkeypatch):
    s = _Store(visible=[])
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk", json={"signal_ids": [], "status": "done"}
    )
    assert r.status_code == 422
    assert s.statements == []


def test_a_non_uuid_id_is_rejected_by_pydantic_before_sql(monkeypatch):
    s = _Store(visible=[])
    r = _client(monkeypatch, s).post(
        "/signals/triage/bulk",
        json={"signal_ids": ["../../etc/passwd"], "status": "done"},
    )

    assert r.status_code == 422
    assert s.statements == []


def test_db_failure_is_a_500_and_does_not_log_the_ids(monkeypatch, caplog):
    s = _Store(visible=[A], raise_on_fetch=RuntimeError("db down"))
    ids = [A, B, C]

    with caplog.at_level("ERROR"):
        r = _client(monkeypatch, s).post(
            "/signals/triage/bulk", json={"signal_ids": ids, "status": "done"}
        )

    assert r.status_code == 500
    assert r.json()["detail"] == "triage_update_failed"
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "3 signals" in logged, "the COUNT is logged"
    for sid in ids:
        assert sid not in logged, "a 200-id dump per failure is noise"


def test_the_bulk_route_does_not_collide_with_the_single_signal_route(monkeypatch):
    """Both resolve to their own handler: the third segment differs
    (literal ``triage`` vs literal ``bulk``), so neither can match the other's
    pattern in either declaration order."""
    from routes import signals as signals_routes

    paths = {r.path for r in signals_routes.router.routes}
    assert "/signals/triage/bulk" in paths
    assert "/signals/{signal_id}/triage" in paths
