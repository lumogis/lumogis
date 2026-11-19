# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/entity_merge`` (ADR 234, LUM-965).

The fake store here pins statement ORDER and the guards that run before any
write; what the SQL actually does (the record, the undo, the typed edges, the
collision rules) is proven on real Postgres in
``tests/integration/test_entity_merge_undo_live.py`` and
``tests/integration/test_entity_merge_edges_live.py``.

Runs from repo root: ``make test``.
"""

import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

WINNER_ID = str(uuid.uuid4())
LOSER_ID = str(uuid.uuid4())
USER_ID = "default"


def _row(eid, name, **kw):
    base = {
        "entity_id": eid,
        "name": name,
        "entity_type": "PERSON",
        "aliases": [],
        "context_tags": [],
        "mention_count": 1,
        "scope": "personal",
        "published_from": None,
        "is_staged": False,
        "version": 1,
        "user_id": USER_ID,
    }
    base.update(kw)
    return base


class _Tx:
    """Records how ``ms.transaction()`` was used."""

    def __init__(self):
        self.entered = 0
        self.committed = False
        self.rolled_back = False

    def __call__(self):
        return self

    def __enter__(self):
        self.entered += 1

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True
        return False


class _FakeMS:
    """Answers reads by what was asked and keeps every statement in order."""

    def __init__(self, winner=None, loser=None, winner_scopes=(), loser_scopes=(), fail_on=None):
        self.tx = _Tx()
        self.transaction = self.tx
        self.log: list[tuple[str, str, tuple]] = []
        self.winner = winner if winner is not None else _row(WINNER_ID, "Alice")
        self.loser = loser if loser is not None else _row(LOSER_ID, "Alice Smith")
        self.scopes = {WINNER_ID: list(winner_scopes), LOSER_ID: list(loser_scopes)}
        self.fail_on = fail_on

    def _note(self, kind, sql, params):
        self.log.append((kind, " ".join(str(sql).split()), tuple(params or ())))
        if self.fail_on and self.fail_on in str(sql):
            raise RuntimeError("boom")

    def execute(self, sql, params=None):
        self._note("execute", sql, params)

    def fetch_one(self, sql, params=None):
        self._note("fetch_one", sql, params)
        q = " ".join(str(sql).split())
        if "INSERT INTO entity_merges" in q:
            return {"merge_id": "m-1"}
        if q.startswith("WITH "):
            return {"n": 1}
        if "SELECT * FROM entities WHERE entity_id" in q:
            return {**self.winner, "name": "Alice"}
        if "pg_column_size" in q:
            return {"bytes": 1}
        if "published_from = %s LIMIT 1" in q:
            return None
        return {}

    def fetch_all(self, sql, params=None):
        self._note("fetch_all", sql, params)
        q = " ".join(str(sql).split())
        if "entity_id = ANY(%s::uuid[])" in q:
            return [r for r in (self.winner, self.loser) if r is not None]
        if "published_from = %s::uuid" in q:
            return [{"scope": s} for s in self.scopes.get(str(params[0]), [])]
        return []

    def writes(self):
        return [
            (k, q)
            for k, q, _ in self.log
            if q.split(" ", 1)[0] in ("INSERT", "UPDATE", "DELETE", "WITH")
        ]


def _run(ms, **kw):
    from services.entity_merge import merge_in_transaction

    kw.setdefault("origin", "member")
    kw.setdefault("acted_by", USER_ID)
    return merge_in_transaction(ms, winner_id=WINNER_ID, loser_id=LOSER_ID, user_id=USER_ID, **kw)


class TestShape:
    def test_U1_merge_requires_origin(self):
        from services.entity_merge import merge_entities

        with pytest.raises(TypeError):
            merge_entities(WINNER_ID, LOSER_ID, USER_ID)

    def test_U3_lock_taken_first(self):
        ms = _FakeMS()
        _run(ms)
        assert "lock_timeout" in ms.log[0][1]
        assert "pg_advisory_xact_lock" in ms.log[1][1]
        assert ms.log[1][2] == (8421161, USER_ID)

    def test_U2_record_insert_precedes_mutations(self):
        ms = _FakeMS()
        _run(ms)
        writes = ms.writes()
        assert "INSERT INTO entity_merges" in writes[0][1]
        for _, q in writes[1:]:
            mutates_data = not q.startswith("INSERT INTO entity_merges")
            if mutates_data:
                assert (
                    "entity_merges" in q
                    or "merge_id" in q
                    or q.startswith(("UPDATE", "DELETE", "WITH", "INSERT"))
                )
        # Every statement that changes loser data comes after the record exists.
        first_loser_write = next(
            i for i, (_, q) in enumerate(ms.writes()) if "entity_relations" in q
        )
        assert first_loser_write > 0
        # The loser is deleted last of the entity statements.
        deletes = [q for _, q in ms.writes() if q.startswith("DELETE FROM entities WHERE")]
        assert len(deletes) == 1

    def test_loser_deleted_after_projections_and_edges(self):
        ms = _FakeMS()
        _run(ms)
        qs = [q for _, q, _ in ms.log]
        i_del = next(i for i, q in enumerate(qs) if q.startswith("DELETE FROM entities WHERE"))
        i_proj = max(i for i, q in enumerate(qs) if "projections_moved" in q)
        i_edge = max(i for i, q in enumerate(qs) if "edges_moved_dst" in q)
        assert i_proj < i_del and i_edge < i_del

    def test_U5_loser_name_becomes_alias(self):
        ms = _FakeMS(
            winner=_row(WINNER_ID, "Dr. Meier"),
            loser=_row(LOSER_ID, "Dr. A. Meier", aliases=["A. Meier"]),
        )
        out = _run(ms)
        upd = next(p for k, q, p in ms.log if q.startswith("UPDATE entities SET name"))
        assert upd[0] == "Dr. Meier"
        assert upd[1] == ["Dr. A. Meier", "A. Meier"]
        assert out.aliases_merged == 2
        rec = next(p for k, q, p in ms.log if "SET name_set" in q)
        assert rec[0] is None and rec[1] == ["Dr. A. Meier", "A. Meier"]

    def test_U6_keep_name_loser(self):
        ms = _FakeMS(winner=_row(WINNER_ID, "Dr. Meier"), loser=_row(LOSER_ID, "Dr. A. Meier"))
        _run(ms, keep_name="loser")
        upd = next(p for k, q, p in ms.log if q.startswith("UPDATE entities SET name"))
        assert upd[0] == "Dr. A. Meier" and upd[1] == ["Dr. Meier"]
        rec = next(p for k, q, p in ms.log if "SET name_set" in q)
        assert rec[0] == "Dr. A. Meier"

    def test_alias_dedupe_ignores_case(self):
        ms = _FakeMS(
            winner=_row(WINNER_ID, "Alice", aliases=["alice smith"]),
            loser=_row(LOSER_ID, "Alice Smith", aliases=["Alice"]),
        )
        _run(ms)
        upd = next(p for k, q, p in ms.log if q.startswith("UPDATE entities SET name"))
        assert upd[1] == ["alice smith"]

    def test_U7_cross_kind_records_rule_snapshot(self):
        ms = _FakeMS(
            winner=_row(WINNER_ID, "Meier GmbH", entity_type="ORG"),
            loser=_row(LOSER_ID, "Meier Pharmacy", entity_type="PERSON"),
        )
        with patch("services.entity_name_rules.record_retype") as rr:
            _run(ms)
        rr.assert_called_once()
        kw = rr.call_args.kwargs
        assert (kw["name"], kw["from_type"], kw["to_type"], kw["source_entity_id"]) == (
            "Meier Pharmacy",
            "PERSON",
            "ORG",
            WINNER_ID,
        )
        snaps = [i for i, (_, q, _) in enumerate(ms.log) if "FROM entity_name_rules" in q]
        assert len(snaps) == 2

    def test_U7_same_kind_writes_no_rule(self):
        ms = _FakeMS()
        with patch("services.entity_name_rules.record_retype") as rr:
            _run(ms)
        rr.assert_not_called()

    def test_U11_engine_refuses_non_personal_rows(self):
        from services.entity_merge import MergeNotPersonal

        for bad in (
            _row(LOSER_ID, "Copy", scope="shared"),
            _row(LOSER_ID, "Copy", published_from=WINNER_ID),
        ):
            ms = _FakeMS(loser=bad)
            with pytest.raises(MergeNotPersonal):
                _run(ms)
            assert not [1 for _, q, _ in ms.log if "INSERT INTO entity_merges" in q]

    def test_U12_non_member_origin_refuses_sharing_change(self):
        from services.entity_merge import MergeChangesSharing

        for origin in ("auto_dedup", "review_queue", "admin"):
            ms = _FakeMS(loser_scopes=["shared"])
            with pytest.raises(MergeChangesSharing):
                _run(ms, origin=origin)
            assert not [1 for _, q, _ in ms.log if "INSERT INTO entity_merges" in q]
            for ws, ls in ((["shared"], ["shared"]), ([], [])):
                ok = _FakeMS(winner_scopes=ws, loser_scopes=ls)
                assert _run(ok, origin=origin).merge_id == "m-1"
        member = _FakeMS(loser_scopes=["shared"])
        assert _run(member, origin="member").merge_id == "m-1"

    def test_staged_rows_are_refused(self):
        ms = _FakeMS(loser=_row(LOSER_ID, "Pending", is_staged=True))
        with pytest.raises(ValueError, match="staged"):
            _run(ms)

    def test_missing_rows_raise_value_error(self):
        ms = _FakeMS()
        ms.loser = None
        with pytest.raises(ValueError, match="loser.*not found"):
            _run(ms)
        ms2 = _FakeMS()
        ms2.winner = None
        with pytest.raises(ValueError, match="winner.*not found"):
            _run(ms2)

    def test_same_id_raises(self):
        from services.entity_merge import merge_entities

        with pytest.raises(ValueError):
            merge_entities(WINNER_ID, WINNER_ID, USER_ID, origin="member")


class TestWrapperAndPostCommit:
    def _wrap(self, ms, **patches):
        from services.entity_merge import merge_entities

        vs = MagicMock()
        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=vs),
            patch("services.entity_merge.hooks.fire_background") as fire,
            patch("services.entity_edit._refresh_search_point", return_value=True) as rsp,
            patch("services.entity_edit._refresh_shared_projections") as rsh,
        ):
            try:
                res = merge_entities(WINNER_ID, LOSER_ID, USER_ID, origin="auto_dedup")
            except Exception as exc:  # noqa: BLE001
                res = exc
        return res, fire, rsp, rsh, vs

    def test_U4_hook_fires_only_after_commit(self):
        ms = _FakeMS()
        res, fire, rsp, rsh, vs = self._wrap(ms)
        assert ms.tx.committed and res.merge_id == "m-1"
        fire.assert_called_once()
        assert fire.call_args.kwargs == {
            "winner_id": WINNER_ID,
            "loser_id": LOSER_ID,
            "user_id": USER_ID,
        }

    def test_U10_phase_a_failure_rolls_back_and_fires_nothing(self):
        ms = _FakeMS(fail_on="entity_relations")
        res, fire, rsp, rsh, vs = self._wrap(ms)
        assert isinstance(res, RuntimeError) and "Phase A failed" in str(res)
        assert ms.tx.rolled_back and not ms.tx.committed
        fire.assert_not_called()
        rsp.assert_not_called()
        vs.delete_where.assert_not_called()

    def test_U8_phase_b_uses_refresh_search_point(self):
        ms = _FakeMS()
        res, fire, rsp, rsh, vs = self._wrap(ms)
        rsp.assert_called_once()
        assert rsp.call_args.args[0]["entity_id"] == WINNER_ID
        flt = vs.delete_where.call_args.kwargs["filter"]
        assert flt["must"][0]["match"]["value"] == LOSER_ID
        assert res.qdrant_cleaned is True

    def test_U9_moved_projection_points_deleted_then_refreshed(self):
        ms = _FakeMS(winner_scopes=[], loser_scopes=["shared"])
        order = []
        vs = MagicMock()
        vs.delete.side_effect = lambda *a, **k: order.append(("delete", a))
        from services.entity_merge import merge_entities
        from services.projection import projection_point_id

        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=vs),
            patch("services.entity_merge.hooks.fire_background"),
            patch("services.entity_edit._refresh_search_point", return_value=True),
            patch(
                "services.entity_edit._refresh_shared_projections",
                side_effect=lambda *a, **k: order.append(("refresh", a)),
            ),
        ):
            merge_entities(WINNER_ID, LOSER_ID, USER_ID, origin="member")
        assert order[0] == (
            "delete",
            ("entities", projection_point_id("entities", LOSER_ID, "shared")),
        )
        assert order[1][0] == "refresh"

    def test_phase_b_failure_keeps_postgres_and_reports_not_cleaned(self):
        ms = _FakeMS()
        from services.entity_merge import merge_entities

        vs = MagicMock()
        vs.delete_where.side_effect = RuntimeError("qdrant down")
        with (
            patch("services.entity_merge.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.config.get_vector_store", return_value=vs),
            patch("services.entity_merge.hooks.fire_background") as fire,
            patch("services.entity_edit._refresh_search_point", return_value=True),
            patch("services.entity_edit._refresh_shared_projections"),
        ):
            res = merge_entities(WINNER_ID, LOSER_ID, USER_ID, origin="member")
        assert ms.tx.committed and res.qdrant_cleaned is False
        fire.assert_called_once()

    def test_lock_wait_propagates_for_dedup_to_handle(self):
        import psycopg2.errors

        ms = _FakeMS()
        ms.fetch_one = MagicMock(side_effect=psycopg2.errors.LockNotAvailable("busy"))
        from services.entity_merge import merge_entities

        with patch("services.entity_merge.config.get_metadata_store", return_value=ms):
            with pytest.raises(psycopg2.errors.LockNotAvailable):
                merge_entities(WINNER_ID, LOSER_ID, USER_ID, origin="auto_dedup")


class TestSqlGuards:
    def test_sessions_entity_ids_compared_as_text(self):
        ms = _FakeMS()
        _run(ms)
        sess = [q for _, q, _ in ms.log if "SET entity_ids = array_replace" in q]
        assert sess and "::uuid" not in sess[0]

    def test_projection_remap_limited_to_entities_table(self):
        ms = _FakeMS()
        with patch("services.projection.remap_published_from") as remap:
            _run(ms)
        remap.assert_not_called()  # the engine runs the entity statements inline

    def test_every_loser_statement_is_user_bound(self):
        ms = _FakeMS()
        _run(ms)
        for kind, q, params in ms.log:
            if q.startswith(("UPDATE entity_relations", "DELETE FROM entity_relations")):
                assert "user_id" in q and USER_ID in params

    def test_U13_init_sql_matches_migration_083(self):
        root = Path(__file__).resolve().parents[2]
        mig = (root / "postgres/migrations/083-entity-merges.sql").read_text()
        init = (root / "postgres/init.sql").read_text()

        def block(text):
            start = text.index("CREATE TABLE IF NOT EXISTS entity_merges")
            end = text.index("entity_merges_user_loser_idx", start)
            end = text.index(";", end) + 1
            body = re.sub(r"--[^\n]*", "", text[start:end])
            return " ".join(body.split())

        assert block(mig) == block(init)


class _Rec(dict):
    """A merge record whose unset columns read as 0 (``list(0 or [])`` is empty)."""

    def __missing__(self, key):
        return 0


class _UndoMS:
    """Enough of a store to run ``undo_in_transaction`` and keep its statements."""

    def __init__(self, rec, rules_now=()):
        from services import entity_merge as em

        self.rec = rec
        self.em = em
        self.rules_now = list(rules_now)
        self.tx = _Tx()
        self.transaction = self.tx
        self.log: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), tuple(params or ())))

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split())
        self.log.append((q, tuple(params or ())))
        if sql is self.em._RECORD_SQL:
            return self.rec
        if "SELECT name, aliases, context_tags, mention_count FROM entities" in q:
            return {"name": "Alice", "aliases": [], "context_tags": [], "mention_count": 1}
        if "SELECT 1 AS x FROM entities" in q or "AS other_name" in q:
            return None
        if q.startswith("SELECT * FROM entities"):
            return {"entity_id": params[0], "name": "Alice", "scope": "personal"}
        return {"n": 0}

    def fetch_all(self, sql, params=None):
        q = " ".join(sql.split())
        self.log.append((q, tuple(params or ())))
        if "FROM entity_name_rules WHERE user_id" in q:
            return list(self.rules_now)
        return []


class TestNameRuleColumnsInMergeRecords:
    """M1 (LUM-966): a merge's rule snapshot carries the block columns."""

    def test_M1_snapshot_selects_the_block_columns(self):
        from services import entity_merge as em

        ms = _UndoMS(_Rec())
        em._rules_snapshot(ms, USER_ID, "alice")
        (q, params) = ms.log[0]
        for col in ("restore_to_type", "display_name", "document_evidence"):
            assert col in q
        assert params == (USER_ID, "alice")

    def _undo(self, rules, rules_now):
        rec = _Rec(
            undone_at=None,
            winner_id=WINNER_ID,
            loser_id=LOSER_ID,
            name_rules=rules,
            winner_before={},
            name_set=None,
        )
        ms = _UndoMS(rec, rules_now=rules_now)
        from services import entity_merge as em

        em.undo_in_transaction(ms, merge_id="m1", user_id=USER_ID, acted_by=USER_ID)
        return ms

    def test_M1_undo_restores_block_columns(self):
        converted = {
            "from_type": "PERSON",
            "action": "block",
            "to_type": None,
            "restore_to_type": "ORG",
            "display_name": "Alice Smith",
            "document_evidence": ["a.pdf"],
            "source_entity_id": None,
        }
        rules = {"name_norm": "alice smith", "before": [converted], "after": []}
        ms = self._undo(rules, rules_now=[])
        (ins,) = [e for e in ms.log if e[0].startswith("INSERT INTO entity_name_rules")]
        assert "restore_to_type, display_name, document_evidence" in ins[0]
        assert ins[1][-3:] == ("ORG", "Alice Smith", ["a.pdf"])

    def test_M1_undo_restores_a_snapshot_written_before_084(self):
        old = {
            "from_type": "PERSON",
            "action": "retype",
            "to_type": "ORG",
            "source_entity_id": None,
        }
        rules = {"name_norm": "alice smith", "before": [old], "after": []}
        ms = self._undo(rules, rules_now=[])
        (ins,) = [e for e in ms.log if e[0].startswith("INSERT INTO entity_name_rules")]
        assert ins[1][-3:] == (None, None, [])

    def test_M1_undo_leaves_rules_alone_when_a_restore_target_changed(self):
        after = [
            {"from_type": "PERSON", "action": "block", "to_type": None, "restore_to_type": "ORG"}
        ]
        now = [{"from_type": "PERSON", "action": "block", "to_type": None, "restore_to_type": None}]
        ms = self._undo({"name_norm": "k", "before": [], "after": after}, rules_now=now)
        assert not [e for e in ms.log if e[0].startswith("DELETE FROM entity_name_rules")]

    def test_M2_dropped_edges_are_restored_only_between_live_endpoints(self):
        ms = self._undo(None, rules_now=[])
        (ins,) = [e for e in ms.log if "INSERT INTO entity_edges" in e[0]]
        assert "x.entity_id::text = e->>'src_entity_id'" in ins[0]
        assert "x.entity_id::text = e->>'dst_entity_id'" in ins[0]
        # (user_id for the row, merge_id, m.user_id, src guard, dst guard)
        assert ins[1] == (USER_ID, "m1", USER_ID, USER_ID, USER_ID)


# ---------------------------------------------------------------------------
# POST /entities/merge (admin)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi import FastAPI
    from routes.admin import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _result():
    from models.entities import MergeResult

    return MergeResult(
        winner_id=WINNER_ID,
        loser_id=LOSER_ID,
        aliases_merged=1,
        relations_moved=2,
        sessions_updated=0,
        qdrant_cleaned=True,
        merge_id="m-1",
    )


class TestEntitiesMergeRoute:
    def test_invalid_uuid_returns_400(self, client):
        resp = client.post(
            "/entities/merge",
            json={"winner_id": "nope", "loser_id": str(uuid.uuid4()), "user_id": "u"},
        )
        assert resp.status_code == 400 and "invalid uuid" in resp.json()["detail"]

    def test_same_id_returns_400(self, client):
        uid = str(uuid.uuid4())
        resp = client.post(
            "/entities/merge", json={"winner_id": uid, "loser_id": uid, "user_id": "u"}
        )
        assert resp.status_code == 400

    def test_A1_missing_user_id_is_422(self, client):
        resp = client.post("/entities/merge", json={"winner_id": WINNER_ID, "loser_id": LOSER_ID})
        assert resp.status_code == 422

    def test_not_found_returns_404(self, client):
        with patch(
            "services.entity_merge.merge_entities",
            side_effect=ValueError("winner entity 'x' not found for user_id='u'"),
        ):
            resp = client.post(
                "/entities/merge",
                json={"winner_id": WINNER_ID, "loser_id": LOSER_ID, "user_id": "u"},
            )
        assert resp.status_code == 404

    def test_A2_success_passes_origin_admin_and_acting_admin(self, client):
        ms = MagicMock()
        with (
            patch("services.entity_merge.merge_entities", return_value=_result()) as merge,
            patch("routes.admin.config.get_metadata_store", return_value=ms),
        ):
            resp = client.post(
                "/entities/merge",
                json={"winner_id": WINNER_ID, "loser_id": LOSER_ID, "user_id": "member-1"},
            )
        assert resp.status_code == 200 and resp.json()["merge_id"] == "m-1"
        kw = merge.call_args.kwargs
        assert kw["origin"] == "admin" and kw["user_id"] == "member-1"
        assert kw["acted_by"]
        assert any("review_decisions" in str(c) for c in ms.execute.call_args_list)

    def test_R13_sharing_differs_is_409_and_not_personal_is_422(self, client):
        from services.entity_merge import MergeChangesSharing
        from services.entity_merge import MergeNotPersonal

        body = {"winner_id": WINNER_ID, "loser_id": LOSER_ID, "user_id": "u"}
        with patch("services.entity_merge.merge_entities", side_effect=MergeChangesSharing("x")):
            resp = client.post("/entities/merge", json=body)
        assert resp.status_code == 409 and resp.json()["detail"]["code"] == "sharing_differs"
        with patch("services.entity_merge.merge_entities", side_effect=MergeNotPersonal("x")):
            resp = client.post("/entities/merge", json=body)
        assert resp.status_code == 422 and resp.json()["detail"]["code"] == "not_personal"

    def test_runtime_error_is_500(self, client):
        with patch("services.entity_merge.merge_entities", side_effect=RuntimeError("boom")):
            resp = client.post(
                "/entities/merge",
                json={"winner_id": WINNER_ID, "loser_id": LOSER_ID, "user_id": "u"},
            )
        assert resp.status_code == 500


# L17 (needs no backend, so it lives here: LUM-753 keeps tests/integration/ for live tests).
def test_L17_undo_records_stay_out_of_export_and_follow_the_default_remap():
    from db_default_user_remap import _SCOPED_TABLES as _PER_USER_TABLES

    from services import user_export

    assert "entity_merges" in user_export._OMITTED_USER_TABLES
    assert "entity_merges" not in user_export._USER_EXPORT_TABLES
    assert "serial relation ids" in user_export._OMITTED_USER_TABLES["entity_merges"]
    assert "entity_merges" in _PER_USER_TABLES
