# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``services/entity_edit.py`` — rename + other names (LUM-161).

A fake store dispatches on what each statement *does* (read, lock, collision
check, write), not on SQL text, so the behaviour under test is the rules:
alias deltas, collision scope, version guard, best-effort propagation. The SQL
itself (kind mapping, UUID compare, the advisory lock, ``FOR UPDATE``) is proven
by ``tests/integration/test_entity_browser_live.py`` against real Postgres.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock
from unittest.mock import patch

import psycopg2
import psycopg2.errors
import pytest
from auth import UserContext
from services.entity_edit import EntityNameInUse
from services.entity_edit import EntityNameInvalid
from services.entity_edit import EntityNotEditable
from services.entity_edit import EntityNotFound
from services.entity_edit import EntityNotOwner
from services.entity_edit import EntityStoreUnavailable
from services.entity_edit import EntityVersionConflict
from services.entity_edit import normalise_names
from services.entity_edit import rename_entity

import config as _config
from services import entity_edit

EID = "11111111-1111-4111-9111-111111111111"
USER = "alice"


def _row(**over):
    row = {
        "entity_id": EID,
        "name": "Dr. Meier",
        "entity_type": "PERSON",
        "aliases": ["Meier"],
        "context_tags": ["clinic", "hospital"],
        "mention_count": 3,
        "extraction_quality": 0.9,
        "version": 1,
        "user_id": USER,
        "scope": "personal",
        "published_from": None,
        "is_staged": False,
    }
    row.update(over)
    return row


class FakeStore:
    """Dispatches on intent. Records writes; can inject failures."""

    def __init__(self, row=None):
        self.row = row if row is not None else _row()
        self.visible = dict(self.row)  # what resolve_viewable_entity returns
        self.collision = None
        self.kind_collision = None
        self.collision_queries = 0
        self.projections: list[dict] = []
        self.writes: list[tuple] = []  # (kind, params)
        self.fail_on: dict[str, Exception] = {}
        self.rolled_back = False

    def _maybe_fail(self, kind):
        if kind in self.fail_on:
            raise self.fail_on[kind]

    @contextmanager
    def transaction(self):
        try:
            yield
        except BaseException:
            self.rolled_back = True
            raise

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SET LOCAL lock_timeout"):
            self.writes.append(("lock_timeout", params))
        elif s.startswith("UPDATE entities SET graph_projected_at = NULL"):
            self.writes.append(("projection_stale", params))
        else:  # pragma: no cover - unexpected statement
            raise AssertionError(f"unexpected execute: {s}")

    def fetch_one(self, sql, params=None):
        s = " ".join(sql.split())
        if "allows_shared" in s:
            return {"allows_shared": True}
        if "pg_advisory_xact_lock" in s:
            self._maybe_fail("lock")
            self.writes.append(("lock", params))
            return {}
        if "FROM entity_name_rules" in s:
            # LUM-966 lift_blocks: the member has no block on the names asked.
            self.writes.append(("rules_lookup", params))
            return None
        if "FOR UPDATE" in s:
            return dict(self.row) if self.row is not None else None
        if s.startswith("SELECT entity_id, name, aliases, is_staged, entity_type"):
            self.collision_queries += 1
            self.writes.append(("kind_collision", params))
            return self.kind_collision
        if s.startswith("UPDATE entities SET entity_type"):
            self._maybe_fail("update")
            target, _pk, _uid, version = params
            if version != self.row["version"]:
                return None
            self.row = {**self.row, "entity_type": target, "version": self.row["version"] + 1}
            self.writes.append(("kind_update", {"params": params, "sql": s}))
            return dict(self.row)
        if s.startswith("SELECT entity_id, name, aliases, is_staged"):
            self.collision_queries += 1
            # Like the real query: a hit only when a *queried* form is held by
            # the other entry (its name or one of its aliases).
            if self.collision is None:
                return None
            held = {self.collision["name"].lower(), *(a.lower() for a in self.collision["aliases"])}
            asked = {f.lower() for f in params[2]}
            return self.collision if held & asked else None
        if s.startswith("UPDATE entities SET name"):
            self._maybe_fail("update")
            name, aliases, _pk, _uid, version = params
            if version != self.row["version"]:
                return None
            self.row = {
                **self.row,
                "name": name,
                "aliases": aliases,
                "version": self.row["version"] + 1,
            }
            self.writes.append(("update", {"name": name, "aliases": aliases, "sql": s}))
            return dict(self.row)
        if "FROM entities WHERE" in s and "entity_id::text" in s:
            return self.visible
        raise AssertionError(f"unexpected fetch_one: {s}")

    def fetch_all(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT scope, share_origin"):
            return list(self.projections)
        raise AssertionError(f"unexpected fetch_all: {s}")  # pragma: no cover


@pytest.fixture
def env():
    ms = FakeStore()
    vs = MagicMock()
    emb = MagicMock()
    emb.embed.return_value = [0.0] * 4
    _config._instances["metadata_store"] = ms
    _config._instances["vector_store"] = vs
    _config._instances["embedder"] = emb
    yield ms, vs, emb
    _config._instances.clear()


def _user():
    return UserContext(user_id=USER, allows_shared=True)


def _rename(**kw):
    kw.setdefault("name", None)
    kw.setdefault("aliases_add", [])
    kw.setdefault("aliases_remove", [])
    kw.setdefault("version", 1)
    return rename_entity(_user(), EID, **kw)


def _change_kind(**kw):
    kw.setdefault("entity_type", "ORG")
    kw.setdefault("version", 1)
    return entity_edit.change_entity_kind(_user(), EID, **kw)


# ── normalise_names ──────────────────────────────────────────────────────────


def test_normalise_trims_collapses_dedupes():
    assert normalise_names("  Dr.  Meier ", ["meier ", "Meier", "", "dr. meier", "Hans"]) == (
        "Dr. Meier",
        ["meier", "Hans"],
    )


def test_normalise_empty_name_raises():
    with pytest.raises(EntityNameInvalid) as ei:
        normalise_names("   ", [])
    assert ei.value.reason == "empty_name"


@pytest.mark.parametrize("bad", ["Bob\x00", "Bob‮", "a\x07b", "x⁦y"])
def test_normalise_rejects_control_and_bidi(bad):
    with pytest.raises(EntityNameInvalid) as ei:
        normalise_names(bad, [])
    assert ei.value.reason == "invalid_characters"
    with pytest.raises(EntityNameInvalid):
        normalise_names("Fine", [bad])


def test_normalise_accepts_tab_as_whitespace():
    assert normalise_names("a\tb", []) == ("a b", [])


def test_normalise_too_many_aliases():
    with pytest.raises(EntityNameInvalid) as ei:
        normalise_names("N", [f"a{i}" for i in range(51)])
    assert ei.value.reason == "too_many_aliases"
    assert len(normalise_names("N", [f"a{i}" for i in range(50)])[1]) == 50


# ── rename rules ─────────────────────────────────────────────────────────────


def test_rename_adds_old_name(env):
    ms, _, _ = env
    out = _rename(name="Hans Meier")
    assert out["name"] == "Hans Meier"
    assert "Dr. Meier" in out["aliases"] and "Meier" in out["aliases"]
    assert out["version"] == 2


def test_rename_remove_old_name(env):
    out = _rename(name="Hans Meier", aliases_remove=["dr. meier"])
    assert "Dr. Meier" not in out["aliases"]
    assert out["aliases"] == ["Meier"]


def test_rename_delta_keeps_concurrent_alias(env):
    ms, _, _ = env
    # The dialog opened with aliases ["Meier"]; extraction added "Hans" since
    # (merge UPDATE: no version bump). The delta body must not drop it.
    ms.row["aliases"] = ["Meier", "Hans"]
    out = _rename(aliases_add=["Dr. M"])
    assert set(out["aliases"]) == {"Meier", "Hans", "Dr. M"}


def test_rename_casing_only_no_alias(env):
    ms, _, _ = env
    ms.row.update(name="alex", aliases=[])
    ms.visible = dict(ms.row)
    out = _rename(name="Alex")
    assert out["name"] == "Alex"
    assert out["aliases"] == []
    assert out["version"] == 2
    assert ms.collision_queries == 0  # casing-only: no new surface form to check


def test_rename_noop_returns_without_write(env):
    ms, vs, emb = env
    with patch("services.projection.project_entity") as proj:
        out = _rename(name="Dr. Meier", aliases_add=["Meier"])
    assert out["version"] == 1
    assert not [w for w in ms.writes if w[0] in ("update", "projection_stale")]
    vs.delete_where.assert_not_called()
    vs.upsert.assert_not_called()
    emb.embed.assert_not_called()
    proj.assert_not_called()


def test_rename_version_conflict(env):
    ms, _, _ = env
    ms.row["version"] = 2
    with pytest.raises(EntityVersionConflict) as ei:
        _rename(name="X", version=1)
    assert ei.value.current_version == 2
    assert not [w for w in ms.writes if w[0] == "update"]


def test_rename_name_in_use_only_for_new_forms(env):
    ms, _, _ = env
    # Another entry already carries the OLD name "Dr. Meier" (existing duplicate
    # data). Keeping it as an alias adds no new surface form → no 409.
    ms.collision = {"entity_id": "other", "name": "Dr. Meier", "aliases": [], "is_staged": False}
    out = _rename(name="Hans Meier")
    assert out["name"] == "Hans Meier"

    # Renaming TO a surface form that is new for this entry and used elsewhere → 409.
    ms.collision = {
        "entity_id": "other",
        "name": "Someone",
        "aliases": ["Prof. M"],
        "is_staged": False,
    }
    with pytest.raises(EntityNameInUse) as ei:
        _rename(name="Prof. M", version=2)
    assert ei.value.conflicting_entity_id == "other"
    assert ei.value.conflicting_is_staged is False
    assert ei.value.surface_form == "Prof. M"

    ms.collision = {"entity_id": "stg", "name": "Prof. M", "aliases": [], "is_staged": True}
    with pytest.raises(EntityNameInUse) as ei:
        _rename(name="Prof. M", version=2)
    assert ei.value.conflicting_is_staged is True


def test_rename_collision_query_skipped_without_new_forms(env):
    ms, _, _ = env
    ms.collision = {
        "entity_id": "other",
        "name": "Dr. Meier",
        "aliases": ["Meier"],
        "is_staged": False,
    }
    # Only reorders/removes existing forms: nothing new, so no collision possible.
    out = _rename(aliases_remove=["Meier"])
    assert out["aliases"] == []
    assert ms.collision_queries == 0


def test_rename_sets_graph_projected_at_null(env):
    ms, _, _ = env
    _rename(name="Hans Meier")
    sql = next(w[1]["sql"] for w in ms.writes if w[0] == "update")
    assert "graph_projected_at = NULL" in sql and "version = version + 1" in sql


def test_rename_not_visible_and_not_owner_and_not_editable(env):
    ms, _, _ = env
    ms.visible = None
    with pytest.raises(EntityNotFound):
        _rename(name="X")
    ms.visible = _row(user_id="bob", scope="shared")
    with pytest.raises(EntityNotOwner):
        _rename(name="X")
    ms.visible = _row(scope="shared", published_from="src-1")
    with pytest.raises(EntityNotEditable) as ei:
        _rename(name="X")
    assert ei.value.source_entity_id == "src-1"
    assert not [w for w in ms.writes if w[0] == "update"]


def test_rename_locked_row_gone_is_not_found(env):
    ms, _, _ = env
    ms.row = None
    with pytest.raises(EntityNotFound):
        _rename(name="X")


# ── post-commit refresh ──────────────────────────────────────────────────────


def test_refresh_embeds_before_delete(env):
    ms, vs, emb = env
    calls = MagicMock()
    calls.attach_mock(emb.embed, "embed")
    calls.attach_mock(vs.delete_where, "delete_where")
    calls.attach_mock(vs.upsert, "upsert")
    _rename(name="Hans Meier")
    assert [c[0] for c in calls.mock_calls] == ["embed", "delete_where", "upsert"]
    flt = vs.delete_where.call_args.kwargs["filter"]
    assert flt == {
        "must": [
            {"key": "entity_id", "match": {"value": EID}},
            {"key": "user_id", "match": {"value": USER}},
        ]
    }
    up = vs.upsert.call_args.kwargs
    assert up["payload"]["name"] == "Hans Meier"
    assert up["payload"]["scope"] == "personal"
    assert up["id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, f"entity::{USER}::hans meier"))


def test_refresh_embed_failure_keeps_old_points(env, caplog):
    ms, vs, emb = env
    emb.embed.side_effect = RuntimeError("embedder down")
    with caplog.at_level(logging.ERROR):
        out = _rename(name="Hans Meier")
    assert out["name"] == "Hans Meier"  # the rename itself stands
    vs.delete_where.assert_not_called()
    vs.upsert.assert_not_called()
    assert any("search point refresh failed" in r.message for r in caplog.records)


def test_refresh_upsert_failure_logged(env, caplog):
    ms, vs, emb = env
    vs.upsert.side_effect = RuntimeError("qdrant down")
    with caplog.at_level(logging.ERROR):
        out = _rename(name="Hans Meier")
    assert out["name"] == "Hans Meier"
    vs.delete_where.assert_called_once()
    assert any("search point refresh failed" in r.message for r in caplog.records)


def test_rename_refreshes_each_shared_projection(env):
    ms, _, _ = env
    ms.projections = [
        {"scope": "shared", "share_origin": "document"},
        {"scope": "system", "share_origin": "multiple"},
    ]
    seen = []

    def _proj(src, *, target_scope, actor, share_origin):
        seen.append((target_scope, share_origin))
        if len(seen) == 1:
            raise RuntimeError("first projection fails")

    with patch("services.projection.project_entity", side_effect=_proj) as proj:
        _rename(name="Hans Meier")
    assert proj.call_count == 2  # the failure did not skip the second
    assert seen == [("shared", "document"), ("system", "user")]
    stale = [w for w in ms.writes if w[0] == "projection_stale"]
    assert [w[1][2] for w in stale] == ["system"]  # only the one that refreshed


def test_rename_no_projections_or_staged_row_skips_projection(env):
    ms, _, _ = env
    with patch("services.projection.project_entity") as proj:
        _rename(name="A")
        ms.row["is_staged"] = True
        ms.projections = [{"scope": "shared", "share_origin": "user"}]
        _rename(name="B", version=2)
    proj.assert_not_called()


# ── failures ─────────────────────────────────────────────────────────────────


def test_rename_db_error_raises_store_unavailable(env):
    ms, vs, _ = env
    ms.fail_on["update"] = psycopg2.OperationalError("down")
    with patch("services.projection.project_entity") as proj:
        with pytest.raises(EntityStoreUnavailable):
            _rename(name="Hans Meier")
    assert ms.rolled_back
    vs.upsert.assert_not_called()
    proj.assert_not_called()


def test_rename_programming_error_is_not_503(env):
    ms, _, _ = env
    ms.fail_on["update"] = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        _rename(name="Hans Meier")


def test_rename_lock_timeout_is_store_unavailable(env):
    ms, _, _ = env
    ms.fail_on["lock"] = psycopg2.errors.LockNotAvailable("lock timeout")
    with pytest.raises(EntityStoreUnavailable):
        _rename(name="Hans Meier")
    assert not [w for w in ms.writes if w[0] == "update"]


def test_rename_lock_is_taken_with_timeout_before_the_locked_read(env):
    ms, _, _ = env
    _rename(name="Hans Meier")
    kinds = [w[0] for w in ms.writes]
    assert kinds.index("lock_timeout") < kinds.index("lock") < kinds.index("update")
    lock_params = next(w[1] for w in ms.writes if w[0] == "lock")
    assert lock_params == (entity_edit.ENTITY_NAMES_ADVISORY_KEY1, USER)


# ── change kind (LUM-967) ────────────────────────────────────────────────────

_PK = EID


@pytest.fixture
def kind_env(env):
    """``env`` plus the post-commit collaborators and the rule writer recorded."""
    ms, vs, emb = env
    rules: list[dict] = []

    def _record(ms_arg, **kw):
        # Called inside the transaction: the fake store is mid-``with``.
        rules.append({"in_ms": ms_arg is ms, **kw})

    with (
        patch("services.entity_edit.entity_name_rules.record_retype", side_effect=_record),
        patch("services.entity_edit.entity_constraints.run_batch_constraints") as batch,
        patch("services.projection.project_entity") as proj,
    ):
        yield ms, vs, emb, rules, batch, proj


def test_allowed_kinds_lockstep():
    from models.mcp_write import ENTITY_TYPES
    from plugins.graph.schema import NodeLabel

    assert set(entity_edit.ALLOWED_KINDS) <= set(NodeLabel.ENTITY_TYPE_MAP)
    assert set(entity_edit.ALLOWED_KINDS) <= set(ENTITY_TYPES)


def test_kind_change_updates_type_version_and_stale_flag(kind_env):
    ms, *_ = kind_env
    out = _change_kind()
    assert out["entity_type"] == "ORG" and out["version"] == 2
    (upd,) = [w for w in ms.writes if w[0] == "kind_update"]
    assert upd[1]["params"] == ("ORG", _PK, USER, 1)
    assert "graph_projected_at = NULL" in upd[1]["sql"]
    assert "version = version + 1" in upd[1]["sql"]


def test_kind_change_writes_rule_in_transaction(kind_env):
    ms, _vs, _emb, rules, *_ = kind_env
    _change_kind()
    assert rules == [
        {
            "in_ms": True,
            "user_id": USER,
            "name": "Dr. Meier",
            "from_type": "PERSON",
            "to_type": "ORG",
            "source_entity_id": _PK,
        }
    ]


def test_kind_change_same_kind_case_insensitive_is_noop(kind_env):
    ms, vs, emb, rules, batch, proj = kind_env
    ms.row["entity_type"] = "org"
    out = _change_kind(entity_type="ORG")
    assert out["version"] == 1
    assert not [w for w in ms.writes if w[0] in ("kind_update", "kind_collision")]
    assert rules == []
    emb.embed.assert_not_called()
    vs.upsert.assert_not_called()
    proj.assert_not_called()
    batch.assert_not_called()


@pytest.mark.parametrize("bad", ["PLACE", "CODING_DECISION", "", "  "])
def test_kind_change_invalid_kind_before_db(kind_env, bad):
    ms, *_ = kind_env
    with pytest.raises(entity_edit.EntityKindInvalid):
        _change_kind(entity_type=bad)
    assert ms.writes == []


def test_kind_change_accepts_lowercase_target(kind_env):
    ms, *_ = kind_env
    out = _change_kind(entity_type=" org ")
    assert out["entity_type"] == "ORG"


def test_kind_change_version_conflict(kind_env):
    ms, _vs, _emb, rules, *_ = kind_env
    ms.row["version"] = 3
    with pytest.raises(EntityVersionConflict) as ei:
        _change_kind(version=2)
    assert ei.value.current_version == 3
    assert rules == []


def test_kind_change_update_returning_none_is_version_conflict(kind_env):
    ms, _vs, _emb, rules, *_ = kind_env
    real = ms.fetch_one

    def _fetch(sql, params=None):
        if " ".join(sql.split()).startswith("UPDATE entities SET entity_type"):
            return None
        return real(sql, params)

    ms.fetch_one = _fetch
    with pytest.raises(EntityVersionConflict):
        _change_kind()
    assert rules == []


def test_kind_change_collision_uses_target_kind_and_all_forms(kind_env):
    ms, _vs, _emb, rules, batch, proj = kind_env
    ms.kind_collision = {
        "entity_id": "other",
        "name": "Meier",
        "aliases": [],
        "is_staged": False,
        "entity_type": "ORG",
    }
    with pytest.raises(EntityNameInUse) as ei:
        _change_kind()
    forms = ["Dr. Meier", "Meier"]
    (q,) = [w for w in ms.writes if w[0] == "kind_collision"]
    assert q[1] == (USER, _PK, "ORG", forms, forms)
    assert ei.value.conflicting_entity_type == "ORG"
    assert ei.value.surface_form == "Meier"
    assert ei.value.conflicting_entity_id == "other"
    assert not [w for w in ms.writes if w[0] == "kind_update"]
    assert rules == []
    batch.assert_not_called()
    proj.assert_not_called()


def test_kind_change_head_checks(kind_env):
    ms, *_ = kind_env
    ms.visible = None
    with pytest.raises(EntityNotFound):
        _change_kind()
    ms.visible = _row(user_id="bob")
    with pytest.raises(EntityNotOwner):
        _change_kind()
    ms.visible = _row(scope="shared", published_from="src-1")
    with pytest.raises(EntityNotEditable) as ei:
        _change_kind()
    assert ei.value.source_entity_id == "src-1"
    assert ms.writes == []


def test_kind_change_post_commit_refreshes(kind_env):
    ms, vs, emb, _rules, batch, proj = kind_env
    ms.projections = [{"scope": "shared", "share_origin": "user"}]
    out = _change_kind()
    vs.upsert.assert_called_once()
    assert vs.upsert.call_args.kwargs["payload"]["entity_type"] == "ORG"
    assert "(ORG)" in emb.embed.call_args.args[0]
    proj.assert_called_once()
    assert proj.call_args.args[0]["entity_type"] == "ORG"
    batch.assert_called_once_with([_PK], USER)
    assert out["entity_type"] == "ORG"


def test_kind_change_post_commit_failures_do_not_propagate(kind_env):
    ms, vs, emb, _rules, batch, proj = kind_env
    ms.projections = [{"scope": "shared", "share_origin": "user"}]
    emb.embed.side_effect = RuntimeError("embedder down")
    proj.side_effect = RuntimeError("projection down")
    batch.side_effect = RuntimeError("constraints down")
    out = _change_kind()
    assert out["entity_type"] == "ORG"
    batch.assert_called_once()


def test_kind_change_db_error_is_store_unavailable(kind_env):
    ms, _vs, _emb, rules, batch, _proj = kind_env
    ms.fail_on["update"] = psycopg2.OperationalError("down")
    with pytest.raises(EntityStoreUnavailable):
        _change_kind()
    assert ms.rolled_back
    batch.assert_not_called()


def test_kind_change_lock_before_locked_read(kind_env):
    ms, *_ = kind_env
    _change_kind()
    kinds = [w[0] for w in ms.writes]
    assert kinds.index("lock_timeout") < kinds.index("lock") < kinds.index("kind_collision")
    ms.writes.clear()
    ms.fail_on["lock"] = psycopg2.errors.LockNotAvailable("lock_timeout")
    with pytest.raises(EntityStoreUnavailable):
        _change_kind(version=2)
    assert not [w for w in ms.writes if w[0] == "kind_update"]


def test_collision_sql_text_unchanged_for_rename():
    assert entity_edit._COLLISION_SQL == (
        "SELECT entity_id, name, aliases, is_staged FROM entities "
        "WHERE user_id = %s AND scope = 'personal' AND published_from IS NULL "
        "  AND entity_id <> %s "
        "  AND (lower(name) = ANY(SELECT lower(x) FROM unnest(%s::text[]) x) "
        "       OR EXISTS (SELECT 1 FROM unnest(aliases) a "
        "                  WHERE lower(a) = ANY(SELECT lower(x) FROM unnest(%s::text[]) x))) "
        "ORDER BY mention_count DESC, entity_id LIMIT 1"
    )
    kind_sql = entity_edit._KIND_COLLISION_SQL
    assert kind_sql.count("%s") == entity_edit._COLLISION_SQL.count("%s") + 1
    assert "entity_id <> %s   AND upper(entity_type) = %s   AND (lower(name)" in kind_sql


def test_rename_still_uses_the_unchanged_collision_sql(env):
    ms, *_ = env
    ms.collision = {"entity_id": "o", "name": "Hans", "aliases": [], "is_staged": False}
    with pytest.raises(EntityNameInUse) as ei:
        _rename(name="Hans")
    assert ei.value.conflicting_entity_type is None
    assert ms.collision_queries == 1


def test_kind_change_blank_name_changes_kind_without_rule(kind_env):
    ms, _vs, _emb, rules, *_ = kind_env
    ms.row["name"] = "  "
    out = _change_kind()
    assert out["entity_type"] == "ORG"
    assert [w for w in ms.writes if w[0] == "kind_update"]
    assert rules == []


def test_kind_change_empty_stored_kind_is_concept(kind_env):
    ms, _vs, _emb, rules, *_ = kind_env
    ms.row["entity_type"] = None
    _change_kind(entity_type="PROJECT")
    assert rules[0]["from_type"] == "CONCEPT"


def test_kind_change_empty_stored_kind_to_concept_is_a_noop(kind_env):
    ms, _vs, _emb, rules, *_ = kind_env
    ms.row["entity_type"] = ""
    out = _change_kind(entity_type="CONCEPT")
    assert out["version"] == 1
    assert not [w for w in ms.writes if w[0] == "kind_update"]
    assert rules == []


def test_kind_change_missing_rules_table_is_store_unavailable(env):
    ms, vs, emb = env
    with (
        patch(
            "services.entity_edit.entity_name_rules.record_retype",
            side_effect=psycopg2.errors.UndefinedTable("entity_name_rules"),
        ),
        patch("services.entity_edit.entity_constraints.run_batch_constraints") as batch,
    ):
        with pytest.raises(EntityStoreUnavailable):
            _change_kind()
    assert ms.rolled_back
    vs.upsert.assert_not_called()
    emb.embed.assert_not_called()
    batch.assert_not_called()
