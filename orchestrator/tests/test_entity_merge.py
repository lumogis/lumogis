# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for orchestrator/services/entity_merge.py — Pass 4a KG Quality Pipeline.

Test groups:
  1.  Phase A success — Postgres changes applied correctly
  2.  Phase A failure — rollback, no hook fired, no Qdrant call
  3.  Phase B Qdrant failure — Postgres committed, hook fired, qdrant_cleaned=False
  4.  Same winner/loser ID — 400
  5.  Entity not found — raises ValueError
  6.  Invalid UUID — 400 before DB call (via route test)
  7.  sessions.entity_ids updated via array_replace
  8.  review_decisions row inserted on POST /entities/merge success
  9.  graph_projected_at nulled on winner after merge
  10. POST /entities/merge route — 200, 400, 404, 500

Runs from repo root: make compose-test (or COMPOSE_FILE=docker-compose.yml docker compose run …).
"""

import inspect
import re
import uuid
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

WINNER_ID = str(uuid.uuid4())
LOSER_ID = str(uuid.uuid4())
USER_ID = "default"

_WINNER_ROW = {
    "entity_id": WINNER_ID,
    "name": "Alice",
    "aliases": ["A. Smith"],
    "context_tags": ["finance", "banking"],
    "mention_count": 3,
}
_LOSER_ROW = {
    "entity_id": LOSER_ID,
    "name": "Alice Smith",
    "aliases": ["Alice S."],
    "context_tags": ["banking", "investment"],
    "mention_count": 1,
}
_WINNER_POST_ROW = {
    "name": "Alice",
    "aliases": ["A. Smith", "Alice S."],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _TxRecorder:
    """Records how `ms.transaction()` was used, instead of faking a connection.

    The old helper built a mock psycopg2 connection and the tests asserted
    `conn.commit()` / `conn.rollback()` on it — implementation detail of a
    mechanism LUM-816 removed. `merge_entities` no longer owns a connection; it
    opens `ms.transaction()` and lets the store commit or roll back.

    So the observable behaviour is: was the block entered, and did it exit
    cleanly or by exception? That is what these tests now assert, and it stays
    true whichever way the store implements atomicity underneath.
    """

    def __init__(self):
        self.entered = 0
        self.committed = False
        self.rolled_back = False

    def __call__(self):
        return self

    def __enter__(self):
        self.entered += 1
        return None

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True
        return False


def _make_conn_mock(fail_at: str | None = None):
    """Back-compat shim: returns (tx_recorder, None).

    Kept so the many `conn, cur = _make_conn_mock()` call sites below read
    unchanged; `conn` is now the transaction recorder.
    """
    return _TxRecorder(), None


def _sql_calls(ms):
    """Every statement the merge issued through the store.

    Phase A used to drive raw cursors on `ms._conn`, so tests read
    `cur.execute.call_args_list`. It now goes through the port — `ms.execute`
    for plain statements and `ms.fetch_one`/`ms.fetch_all` for reads and row
    counts — so the SQL lives across the mocks. Collecting them together keeps
    these assertions about *what SQL ran*, which is the behaviour they were
    always checking.
    """
    return (
        list(ms.execute.call_args_list)
        + list(ms.fetch_one.call_args_list)
        + list(ms.fetch_all.call_args_list)
    )


def _make_ms(conn, winner_row=None, loser_row=None, winner_post=None):
    """Return a mock MetadataStore that answers reads by *what was asked*.

    `fetch_one` used to be a fixed four-element `side_effect` sequence, which
    made the fake a model of the implementation's call *order* rather than of a
    store: moving the row counts onto `fetch_one` (a CTE, so Postgres counts
    instead of shipping every affected row) silently fed an entity row to a
    count and a count to an entity lookup, and ten tests failed for a reason
    that had nothing to do with what they were testing.

    Dispatching on the query is what a store actually does, and it survives the
    merge issuing its statements in a different order.
    """
    ms = MagicMock()
    ms.transaction = conn  # _TxRecorder is itself the context-manager factory
    ms.fetch_all.return_value = []

    # Entity reads, in the order the merge needs them:
    # winner verify, loser verify, winner pre-fetch, winner post-update.
    entity_rows = [
        winner_row,
        loser_row,
        winner_row,
        winner_post or {"name": (winner_row or {}).get("name", ""), "aliases": []},
    ]

    def _fetch_one(sql, params=None):
        if str(sql).lstrip().startswith("WITH affected AS"):
            return {"n": 1}  # one row touched by this UPDATE/DELETE
        return entity_rows.pop(0) if entity_rows else None

    ms.fetch_one.side_effect = _fetch_one
    return ms


# ---------------------------------------------------------------------------
# 1. Phase A success
# ---------------------------------------------------------------------------


class TestPhaseASuccess:
    def test_all_postgres_steps_called(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background") as mock_hook,
        ):
            from services.entity_merge import merge_entities

            result = merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        assert result.winner_id == WINNER_ID
        assert result.loser_id == LOSER_ID
        # Phase A committed
        assert conn.committed, "the transaction should have committed"
        # Hook fired after commit
        mock_hook.assert_called_once()
        call_kwargs = mock_hook.call_args
        assert call_kwargs.kwargs.get("winner_id") == WINNER_ID
        assert call_kwargs.kwargs.get("loser_id") == LOSER_ID

    def test_mentions_count_summed(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        # Check UPDATE entities called with summed mention_count
        update_calls = [c for c in _sql_calls(ms) if "UPDATE entities" in (c.args[0] or "")]
        assert update_calls, "UPDATE entities not called"
        update_params = update_calls[0].args[1]
        assert 4 in update_params  # 3 + 1 = 4 mention_count

    def test_graph_projected_at_nulled_on_winner(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        # The UPDATE entities SQL must set graph_projected_at = NULL
        all_sql = " ".join(str(c.args[0]) for c in _sql_calls(ms) if c.args)
        assert "graph_projected_at = NULL" in all_sql

    def test_loser_deleted(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        delete_calls = [c for c in _sql_calls(ms) if "DELETE FROM entities" in (c.args[0] or "")]
        assert delete_calls, "DELETE FROM entities not called"
        assert LOSER_ID in delete_calls[0].args[1]

    def test_sessions_updated_via_array_replace(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        session_calls = [c for c in _sql_calls(ms) if "array_replace" in (c.args[0] or "")]
        assert session_calls, "array_replace UPDATE sessions not called"
        sql = session_calls[0].args[0]
        assert "entity_ids" in sql

    def test_entity_relations_moved_to_winner(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        relation_calls = [
            c for c in _sql_calls(ms) if "UPDATE entity_relations" in (c.args[0] or "")
        ]
        assert relation_calls, "UPDATE entity_relations not called"
        params = relation_calls[0].args[1]
        assert WINNER_ID in params
        assert LOSER_ID in params

    def test_qdrant_cleaned_true_on_success(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
        mock_vs = MagicMock()
        mock_vs.delete_where = MagicMock()
        mock_vs.upsert = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.0] * 768

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=mock_vs),
            patch("services.entity_merge.config.get_embedder", return_value=mock_embedder),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            result = merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        assert result.qdrant_cleaned is True
        mock_vs.delete_where.assert_called_once()
        mock_vs.upsert.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Phase A failure — rollback, no hook, no Qdrant
# ---------------------------------------------------------------------------


class TestPhaseAFailure:
    def test_rollback_on_sql_error(self):
        conn, cur = _make_conn_mock()
        ms = MagicMock()
        ms.transaction = conn
        ms.fetch_all.return_value = [{"?column?": 1}, {"?column?": 1}]
        # Make fetch_one raise after winner found
        ms.fetch_one.side_effect = [_WINNER_ROW, RuntimeError("constraint violation")]

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.hooks.fire_background") as mock_hook,
        ):
            from services.entity_merge import merge_entities

            with pytest.raises(RuntimeError):
                merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        assert conn.rolled_back, "the transaction should have rolled back"
        mock_hook.assert_not_called()

    def test_no_qdrant_call_on_phase_a_failure(self):
        conn, cur = _make_conn_mock()
        ms = MagicMock()
        ms.transaction = conn
        ms.fetch_all.return_value = [{"?column?": 1}, {"?column?": 1}]
        ms.fetch_one.side_effect = [_WINNER_ROW, RuntimeError("db error")]

        mock_vs = MagicMock()

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=mock_vs),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            from services.entity_merge import merge_entities

            with pytest.raises(RuntimeError):
                merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        mock_vs.delete_where.assert_not_called()
        mock_vs.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Phase B Qdrant failure
# ---------------------------------------------------------------------------


class TestPhaseBQdrantFailure:
    def test_postgres_committed_hook_fired_qdrant_cleaned_false(self):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)

        mock_vs = MagicMock()
        mock_vs.delete_where.side_effect = RuntimeError("Qdrant down")

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=mock_vs),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background") as mock_hook,
        ):
            from services.entity_merge import merge_entities

            result = merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        # Phase A committed
        assert conn.committed, "the transaction should have committed"
        # Hook fired
        mock_hook.assert_called_once()
        # qdrant_cleaned False
        assert result.qdrant_cleaned is False


# ---------------------------------------------------------------------------
# 4. Same winner/loser ID
# ---------------------------------------------------------------------------


class TestSameId:
    def test_raises_value_error(self):
        from services.entity_merge import merge_entities

        with pytest.raises(ValueError, match="differ"):
            merge_entities(WINNER_ID, WINNER_ID, USER_ID)


# ---------------------------------------------------------------------------
# 5. Entity not found
# ---------------------------------------------------------------------------


class TestEntityNotFound:
    def test_winner_not_found_raises_value_error(self):
        conn, _ = _make_conn_mock()
        ms = MagicMock()
        ms.transaction = conn
        ms.fetch_all.return_value = [{"?column?": 1}, {"?column?": 1}]
        ms.fetch_one.return_value = None  # winner not found

        with patch("services.entity_merge.config.get_metadata_store", return_value=ms):
            from services.entity_merge import merge_entities

            with pytest.raises(ValueError, match="winner.*not found"):
                merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        assert conn.rolled_back, "the transaction should have rolled back"

    def test_loser_not_found_raises_value_error(self):
        conn, _ = _make_conn_mock()
        ms = MagicMock()
        ms.transaction = conn
        ms.fetch_all.return_value = [{"?column?": 1}, {"?column?": 1}]
        # sequence: winner_pre (merge_entities), winner_row (_run_phase_a), loser_row (_run_phase_a)
        ms.fetch_one.side_effect = [_WINNER_ROW, _WINNER_ROW, None]  # loser not found

        with patch("services.entity_merge.config.get_metadata_store", return_value=ms):
            from services.entity_merge import merge_entities

            with pytest.raises(ValueError, match="loser.*not found"):
                merge_entities(WINNER_ID, LOSER_ID, USER_ID)

        assert conn.rolled_back, "the transaction should have rolled back"


# ---------------------------------------------------------------------------
# 6–10. Route-level tests for POST /entities/merge
# ---------------------------------------------------------------------------


def _make_app():
    from fastapi import FastAPI
    from routes.admin import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    app = _make_app()
    return TestClient(app, raise_server_exceptions=False)


class TestEntitiesMergeRoute:
    def test_invalid_uuid_returns_400(self, client):
        resp = client.post(
            "/entities/merge", json={"winner_id": "not-a-uuid", "loser_id": str(uuid.uuid4())}
        )
        assert resp.status_code == 400
        assert "invalid uuid" in resp.json()["detail"]

    def test_same_id_returns_400(self, client):
        uid = str(uuid.uuid4())
        resp = client.post("/entities/merge", json={"winner_id": uid, "loser_id": uid})
        assert resp.status_code == 400

    def test_entity_not_found_returns_404(self, client):
        conn, _ = _make_conn_mock()
        ms = MagicMock()
        ms.transaction = conn
        ms.fetch_all.return_value = [{"?column?": 1}, {"?column?": 1}]
        ms.fetch_one.return_value = None

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            with patch("services.entity_merge.config.get_metadata_store", return_value=ms):
                resp = client.post(
                    "/entities/merge",
                    json={
                        "winner_id": WINNER_ID,
                        "loser_id": LOSER_ID,
                    },
                )
        assert resp.status_code == 404

    def test_successful_merge_returns_200(self, client):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
        mock_vs = MagicMock()
        mock_vs.delete_where = MagicMock()
        mock_vs.upsert = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.0] * 768

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("routes.admin.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=mock_vs),
            patch("services.entity_merge.config.get_embedder", return_value=mock_embedder),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            resp = client.post(
                "/entities/merge",
                json={
                    "winner_id": WINNER_ID,
                    "loser_id": LOSER_ID,
                    "user_id": USER_ID,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["winner_id"] == WINNER_ID
        assert data["loser_id"] == LOSER_ID

    def test_review_decisions_row_inserted_on_success(self, client):
        conn, cur = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
        mock_vs = MagicMock()
        mock_vs.delete_where = MagicMock()
        mock_vs.upsert = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.0] * 768

        audit_calls = []

        def _capture_execute(sql, params=None):
            if sql and "review_decisions" in sql:
                audit_calls.append((sql, params))

        ms.execute.side_effect = _capture_execute

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("routes.admin.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=mock_vs),
            patch("services.entity_merge.config.get_embedder", return_value=mock_embedder),
            patch("services.entity_merge.hooks.fire_background"),
        ):
            resp = client.post(
                "/entities/merge",
                json={
                    "winner_id": WINNER_ID,
                    "loser_id": LOSER_ID,
                    "user_id": USER_ID,
                },
            )

        assert resp.status_code == 200
        assert audit_calls, "review_decisions INSERT not called"


# ---------------------------------------------------------------------------
# Migration 012 — entity_relations evidence dedup contract: merge collision
# See .cursor/plans/entity_relations_evidence_dedup.plan.md
# ---------------------------------------------------------------------------


def test_merge_entities_handles_duplicate_evidence_collision():
    """`_run_phase_a` Step 2 must use a two-pass UPDATE-NOT-EXISTS + DELETE
    to avoid violating the post-012 unique constraint when winner and loser
    both have rows for the same (evidence_id, relation_type, user_id) triple.

    A naive UPDATE entity_relations SET source_id = winner WHERE
    source_id = loser would raise unique_violation in Postgres post-012.
    The required shape is:

      UPDATE entity_relations SET source_id = %s
        WHERE source_id = %s AND user_id = %s
          AND NOT EXISTS (
            SELECT 1 FROM entity_relations er2
             WHERE er2.source_id = %s
               AND er2.evidence_id = entity_relations.evidence_id
               AND er2.relation_type = entity_relations.relation_type
               AND er2.user_id = entity_relations.user_id)
      ;
      DELETE FROM entity_relations WHERE source_id = %s AND user_id = %s;

    The function must also return relations_dropped_as_duplicate so the
    caller can emit a log line (log-only — not on the MergeResult API).

    This test inspects the source of `_run_phase_a` specifically (not the
    whole module) to keep the regex matches scoped — false positives from
    SQL elsewhere in the module would defeat the gate.
    """
    from services import entity_merge

    source = inspect.getsource(entity_merge._run_phase_a)
    # Collapse Python string-concatenation whitespace and quote chars so
    # SQL patterns match across split adjacent string literals (e.g.
    # `"WHERE source_id = %s AND user_id = %s "\n"  AND NOT EXISTS ("`).
    flat = re.sub(r"['\"\s]+", " ", source)

    # 1. UPDATE carries the WHERE NOT EXISTS subquery on entity_relations.
    update_pattern = re.compile(
        r"UPDATE\s+entity_relations\s+SET\s+source_id\s*=\s*%s.*?"
        r"WHERE\s+source_id\s*=\s*%s\s+AND\s+user_id\s*=\s*%s.*?"
        r"AND\s+NOT\s+EXISTS\s*\(\s*"
        r"SELECT\s+1\s+FROM\s+entity_relations\s+er2",
        re.IGNORECASE | re.DOTALL,
    )
    assert update_pattern.search(flat), (
        "_run_phase_a Step 2 UPDATE must guard against migration-012 unique "
        "violations with a WHERE NOT EXISTS subquery against entity_relations. "
        "A naive UPDATE will raise unique_violation when winner and loser "
        "share an (evidence_id, relation_type, user_id) triple."
    )

    # 2. DELETE removes the loser's remaining (collision) rows.
    delete_pattern = re.compile(
        r"DELETE\s+FROM\s+entity_relations\s+WHERE\s+source_id\s*=\s*%s\s+"
        r"AND\s+user_id\s*=\s*%s",
        re.IGNORECASE,
    )
    assert delete_pattern.search(flat), (
        "_run_phase_a Step 2 must DELETE remaining loser entity_relations "
        "rows after the guarded UPDATE — these are the rows whose re-point "
        "would have collided with an existing winner row."
    )

    # 3. UPDATE must come BEFORE DELETE in source order. A reversed order
    #    would delete-then-update-zero-rows and silently lose data.
    update_pos = update_pattern.search(flat).start()
    delete_pos = delete_pattern.search(flat).start()
    assert update_pos < delete_pos, (
        f"_run_phase_a Step 2 must UPDATE before DELETE (got UPDATE at "
        f"{update_pos}, DELETE at {delete_pos}). Reversed order would lose "
        f"all loser relations instead of re-pointing the unique ones."
    )

    # 4. relations_dropped_as_duplicate must appear in the function's return
    #    dict — the caller relies on it for the merge log line. Inspect the
    #    raw source (not flattened) so the colon-separated dict syntax is
    #    preserved.
    return_dict_pattern = re.compile(
        r"['\"]relations_dropped_as_duplicate['\"]\s*:\s*relations_dropped_as_duplicate",
    )
    assert return_dict_pattern.search(source), (
        "_run_phase_a must return relations_dropped_as_duplicate so "
        "merge_entities can emit it in its 'Phase A committed' log line."
    )


# ---------------------------------------------------------------------------
# 11. entity_edges repoint (LUM-598)
# ---------------------------------------------------------------------------


def _run_merge_collect_sql():
    conn, _ = _make_conn_mock()
    ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
    with (
        patch("services.entity_merge.config.get_metadata_store", return_value=ms),
        patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
        patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
        patch("services.entity_merge.hooks.fire_background"),
    ):
        from services.entity_merge import merge_entities

        merge_entities(WINNER_ID, LOSER_ID, USER_ID)
    return ms, [(" ".join(str(c.args[0]).split()), c.args[1]) for c in _sql_calls(ms)]


class TestEntityEdgesRepoint:
    def _edge_sql(self, calls):
        return [(q, p) for q, p in calls if "entity_edges" in q]

    def test_src_and_dst_repointed_in_merge_transaction_before_loser_delete(self):
        ms, _ = _run_merge_collect_sql()
        ordered = [" ".join(str(c.args[0]).split()) for c in ms.method_calls if c.args]
        src_i = next(i for i, q in enumerate(ordered) if "SET src_entity_id = %s" in q)
        dst_i = next(i for i, q in enumerate(ordered) if "SET dst_entity_id = %s" in q)
        # The loser DELETE is the last statement of Phase A that hits `entities`.
        del_i = max(i for i, q in enumerate(ordered) if q.startswith("DELETE FROM entities"))
        assert src_i < del_i and dst_i < del_i

    def test_repoint_params_and_user_scoping(self):
        _, calls = _run_merge_collect_sql()
        edge = self._edge_sql(calls)
        src = [(q, p) for q, p in edge if "SET src_entity_id = %s" in q]
        dst = [(q, p) for q, p in edge if "SET dst_entity_id = %s" in q]
        assert len(src) == 1 and len(dst) == 1
        # (winner, user, loser, winner): move loser -> winner for this user only.
        assert src[0][1] == (WINNER_ID, USER_ID, LOSER_ID, WINNER_ID)
        assert dst[0][1] == (WINNER_ID, USER_ID, LOSER_ID, WINNER_ID)
        assert all("user_id = %s" in q for q, _ in edge)

    def test_only_active_edges_move_and_every_bank_is_covered(self):
        _, calls = _run_merge_collect_sql()
        for q, _ in self._edge_sql(calls):
            if "SET src_entity_id" in q or "SET dst_entity_id" in q:
                assert "valid_until IS NULL" in q
                # Banks are matched pairwise (not filtered) so all banks move.
                assert "w.bank = e.bank" in q

    def test_repoint_resets_projection_stamp_and_preserves_temporal_columns(self):
        _, calls = _run_merge_collect_sql()
        for q, _ in self._edge_sql(calls):
            if "SET src_entity_id" in q or "SET dst_entity_id" in q:
                set_clause = q.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
                assert "graph_projected_at = NULL" in set_clause
                # LUM-599: a repointed edge gets a fresh reconcile retry budget.
                assert "reconcile_attempts = 0" in set_clause
                assert not re.search(
                    r"\b(valid_from|valid_until|created_at|evidence_id|id|bank) =", set_clause
                )

    def test_duplicates_deleted_against_active_winner_edge_only(self):
        _, calls = _run_merge_collect_sql()
        dele = [q for q, _ in self._edge_sql(calls) if "DELETE FROM entity_edges" in q]
        assert len(dele) == 1
        assert "w.valid_until IS NULL" in dele[0]
        assert "e.valid_until IS NULL" in dele[0]
        # Repoint statements guard on ANY winner row (archived rows hold the UNIQUE slot).
        for q, _ in self._edge_sql(calls):
            if "SET src_entity_id" in q or "SET dst_entity_id" in q:
                assert "w.valid_until" not in q

    def test_selfloops_archived_not_repointed(self):
        _, calls = _run_merge_collect_sql()
        loops = [(q, p) for q, p in self._edge_sql(calls) if "SET valid_until = now()" in q]
        assert len(loops) == 1
        q, params = loops[0]
        assert "valid_until IS NULL" in q
        # loser->loser, loser->winner, winner->loser
        assert params == (USER_ID, LOSER_ID, LOSER_ID, WINNER_ID, WINNER_ID, LOSER_ID)

    def test_selfloop_archive_runs_before_repoint(self):
        _, calls = _run_merge_collect_sql()
        qs = [q for q, _ in self._edge_sql(calls)]
        loop_i = next(i for i, q in enumerate(qs) if "SET valid_until = now()" in q)
        move_i = next(i for i, q in enumerate(qs) if "SET src_entity_id" in q)
        del_i = next(i for i, q in enumerate(qs) if "DELETE FROM entity_edges" in q)
        assert loop_i < del_i < move_i

    def test_edge_failure_rolls_back_whole_merge(self):
        conn, _ = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
        base = ms.fetch_one.side_effect

        def boom(sql, params=None):
            if "SET dst_entity_id" in str(sql):
                raise RuntimeError("edge repoint failed")
            return base(sql, params)

        ms.fetch_one.side_effect = boom
        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.hooks.fire_background") as hook,
        ):
            from services.entity_merge import merge_entities

            with pytest.raises(RuntimeError):
                merge_entities(WINNER_ID, LOSER_ID, USER_ID)
        assert conn.rolled_back and not conn.committed
        hook.assert_not_called()

    def test_unmerged_collision_with_archived_winner_edge_is_warned(self, caplog):
        conn, _ = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
        base = ms.fetch_one.side_effect

        def counts(sql, params=None):
            if str(sql).startswith("SELECT count(*) AS n FROM entity_edges"):
                return {"n": 2}
            return base(sql, params)

        ms.fetch_one.side_effect = counts
        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
            caplog.at_level("WARNING"),
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)
        assert "archived winner edge" in caplog.text


class TestLiveSqlTypeRegressions:
    """Pins the two statements that failed on every real Postgres (types only a live DB checks)."""

    def test_sessions_entity_ids_compared_as_text(self):
        # sessions.entity_ids is TEXT[] (migration 003): no ::uuid casts.
        _, calls = _run_merge_collect_sql()
        sess = [q for q, _ in calls if "UPDATE sessions" in q]
        assert len(sess) == 1 and "::uuid" not in sess[0]

    def test_projection_remap_limited_to_entities_table(self):
        # file_index.published_from is INTEGER; an entity UUID there aborts the merge.
        conn, _ = _make_conn_mock()
        ms = _make_ms(conn, _WINNER_ROW, _LOSER_ROW, _WINNER_POST_ROW)
        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=MagicMock()),
            patch("services.entity_merge.config.get_embedder", return_value=MagicMock()),
            patch("services.entity_merge.hooks.fire_background"),
            patch("services.projection.remap_published_from") as remap,
        ):
            from services.entity_merge import merge_entities

            merge_entities(WINNER_ID, LOSER_ID, USER_ID)
        remap.assert_called_once_with(LOSER_ID, WINNER_ID, tables=("entities",))
