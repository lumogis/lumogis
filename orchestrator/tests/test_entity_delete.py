# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``entity_edit.delete_entry`` / ``hide_entry`` / ``unhide_entry`` / rule removal (LUM-966).

A fake store answers each statement by what it asks for and keeps every
statement in order, so the transaction's shape (lock → re-read → collect → rule
→ cleanup → delete → postcondition → tombstone, all before the commit) is the
behaviour under test. The SQL itself runs against real Postgres in
``integration/test_entity_delete_live.py``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock

import psycopg2
import pytest
from auth import UserContext
from services.entity_edit import EntityHideOwn
from services.entity_edit import EntityNotEditable
from services.entity_edit import EntityNotFound
from services.entity_edit import EntityNotOwner
from services.entity_edit import EntityStoreUnavailable
from services.entity_edit import EntityVersionConflict
from services.entity_edit import NameRuleNotFound

import config as _config
from services import entity_deletions
from services import entity_edit
from services import entity_name_rules as enr

EID = "11111111-1111-4111-9111-111111111111"
PROJ = "22222222-2222-4222-9222-222222222222"
OWNER_ROW = "33333333-3333-4333-9333-333333333333"
USER = "alice"


def _row(**over):
    row = {
        "entity_id": EID,
        "name": "Total Gmbh",
        "entity_type": "PERSON",
        "aliases": ["Total", "TOTAL"],
        "context_tags": [],
        "mention_count": 3,
        "version": 4,
        "user_id": USER,
        "scope": "personal",
        "published_from": None,
        "is_staged": False,
    }
    row.update(over)
    return row


class FakeStore:
    def __init__(self, row=None):
        self.row = row if row is not None else _row()
        self.visible = dict(self.row)
        self.evidence: list[str] = ["/docs/b.pdf", "/docs/a.pdf"]
        self.projections: list[str] = []
        self.merges = 0
        self.in_use: list[str] = []
        self.corrected: list[str] = []
        self.left_behind = False
        self.log: list[tuple[str, str, tuple]] = []
        self.committed = False
        self.rolled_back = False
        self.fail_on: dict[str, Exception] = {}
        self.file_rows: list[dict] = []
        self.rules: dict = {}
        self.visible_hidden: list[dict] = []

    def _note(self, kind, sql, params):
        s = " ".join(sql.split())
        self.log.append((kind, s, tuple(params or ())))
        for marker, exc in self.fail_on.items():
            if marker in s:
                raise exc
        return s

    @contextmanager
    def transaction(self):
        try:
            yield
        except BaseException:
            self.rolled_back = True
            raise
        self.committed = True

    def execute(self, sql, params=None):
        self._note("execute", sql, params)

    def fetch_one(self, sql, params=None):
        s = self._note("fetch_one", sql, params)
        if "allows_shared" in s:
            return {"allows_shared": True}
        if "pg_advisory_xact_lock" in s:
            return {}
        if "FROM entity_merges" in s and "count(*)" in s:
            return {"n": self.merges}
        if s.startswith("SELECT 1 AS x FROM entities"):
            return {"x": 1} if self.left_behind else None
        if "FROM entity_name_rules" in s:
            return self.rules.get("main")
        if "FOR UPDATE" in s and "FROM entities" in s:
            return dict(self.row) if self.row is not None else None
        if "FROM entities WHERE" in s and "entity_id::text = %s" in s:
            return self.visible
        if s.startswith("SELECT count(*) AS n FROM entity_hidden"):
            return {"n": len(self.visible_hidden)}
        return None

    def fetch_all(self, sql, params=None):
        s = self._note("fetch_all", sql, params)
        if "FROM entity_relations" in s:
            return [{"evidence_id": e} for e in self.evidence]
        if "WHERE published_from = %s AND user_id = %s" in s:
            return [{"entity_id": p} for p in self.projections]
        if s.startswith("SELECT f AS form"):
            return [{"form": f} for f in self.in_use]
        if "FROM entity_name_rules" in s and "action = 'retype'" in s and "name_norm = ANY" in s:
            return [{"name_norm": k} for k in self.corrected]
        if "FROM entity_name_rules" in s:
            return self.rules.get("converted", [])
        if "FROM file_index" in s:
            return self.file_rows
        if "FROM entity_hidden" in s:
            return self.visible_hidden
        return []


@pytest.fixture
def env(monkeypatch):
    ms = FakeStore()
    vs = MagicMock()
    _config._instances["metadata_store"] = ms
    _config._instances["vector_store"] = vs
    fired: list[dict] = []
    monkeypatch.setattr(
        entity_deletions.hooks,
        "fire_background",
        lambda event, **kw: fired.append({"event": event, "committed": ms.committed, **kw}),
    )
    ms.fired = fired
    yield ms, vs
    _config._instances.clear()


def _user():
    return UserContext(user_id=USER, allows_shared=True)


def _delete(**kw):
    kw.setdefault("version", 4)
    return entity_edit.delete_entry(_user(), EID, **kw)


def _sql_index(ms, fragment, after=0):
    for i, (_k, s, _p) in enumerate(ms.log):
        if i >= after and fragment in s:
            return i
    raise AssertionError(f"no statement containing {fragment!r}: {[s for _k, s, _p in ms.log]}")


# ── delete ───────────────────────────────────────────────────────────────────


def test_D1_delete_runs_one_ordered_transaction_and_fires_after_commit(env):
    ms, _vs = env
    _delete()
    order = [
        "SET LOCAL lock_timeout",
        "pg_advisory_xact_lock",
        "FOR UPDATE",
        "FROM entity_relations",
        "FROM entity_merges",
        "INSERT INTO entity_name_rules",
        "DELETE FROM entity_edges",
        "UPDATE sessions SET entity_ids = array_remove",
        "DELETE FROM entity_merges",
        "DELETE FROM entities",
        "SELECT 1 AS x FROM entities",
        "INSERT INTO entity_deletions",
    ]
    at = -1
    for fragment in order:
        at = _sql_index(ms, fragment, after=at + 1)
    assert ms.committed
    assert len(ms.fired) == 1 and ms.fired[0]["committed"] is True  # after the commit


def test_D2_response_names_blocked_forms_canonical_first_and_counts(env):
    ms, _ = env
    ms.projections = [PROJ]
    ms.merges = 2
    out = _delete()
    assert out.entity_id == EID
    assert out.blocked_names == ["Total Gmbh", "Total"]  # "TOTAL" is the same key as "Total"
    assert out.not_blocked == []
    assert out.household_copy_removed is True
    assert out.merges_removed == 2
    (fired,) = ms.fired
    assert fired["entity_id"] == EID and fired["user_id"] == USER
    assert fired["projection_ids"] == [PROJ]


def test_D2b_the_block_rows_carry_kind_names_and_evidence_most_recent_first(env):
    ms, _ = env
    _delete()
    blocks = [p for _k, s, p in ms.log if s.startswith("INSERT INTO entity_name_rules")]
    assert [b[1] for b in blocks] == ["total gmbh", "total"]
    assert all(b[2] == "PERSON" for b in blocks)
    assert blocks[0][4] == "Total Gmbh"  # the display form is the notice's form
    assert blocks[0][5] == ["/docs/b.pdf", "/docs/a.pdf"]


def test_D3_an_alias_another_live_same_kind_entry_uses_is_not_blocked(env):
    ms, _ = env
    ms.in_use = ["Total"]
    out = _delete()
    assert out.not_blocked == [{"name": "Total", "reason": "used_by_other_entry"}]
    assert out.blocked_names == ["Total Gmbh"]
    keys = [p[1] for _k, s, p in ms.log if s.startswith("INSERT INTO entity_name_rules")]
    assert keys == ["total gmbh"]


def test_D4_the_canonical_name_in_use_is_not_blocked_either(env):
    ms, _ = env
    ms.in_use = ["Total Gmbh"]
    out = _delete()
    assert out.not_blocked == [{"name": "Total Gmbh", "reason": "used_by_other_entry"}]
    assert out.blocked_names == ["Total"]


def test_D5_a_name_a_kind_correction_already_sends_elsewhere_is_not_blocked(env):
    ms, _ = env
    ms.corrected = ["total"]
    out = _delete()
    assert out.not_blocked == [{"name": "Total", "reason": "already_corrected"}]
    assert out.blocked_names == ["Total Gmbh"]


def test_D6_a_blank_stored_kind_blocks_at_concept(env):
    ms, _ = env
    ms.row = _row(entity_type="")
    ms.visible = dict(ms.row)
    _delete()
    kinds = {p[2] for _k, s, p in ms.log if s.startswith("INSERT INTO entity_name_rules")}
    assert kinds == {"CONCEPT"}
    forms = [p for _k, s, p in ms.log if s.startswith("SELECT f AS form")]
    assert forms[0][3] == "CONCEPT"


def test_D7_a_stale_version_changes_nothing(env):
    ms, _ = env
    with pytest.raises(EntityVersionConflict) as exc:
        _delete(version=3)
    assert exc.value.current_version == 4
    assert not any(s.startswith(("DELETE", "INSERT", "UPDATE sessions")) for _k, s, _p in ms.log)
    assert ms.rolled_back and ms.fired == []


def test_D8_a_database_error_is_a_503_and_nothing_fires(env):
    ms, _ = env
    ms.fail_on["DELETE FROM entities"] = psycopg2.OperationalError("lock timeout")
    with pytest.raises(EntityStoreUnavailable):
        _delete()
    assert ms.rolled_back and ms.fired == []


def test_D9_a_leftover_row_rolls_the_transaction_back(env):
    ms, _ = env
    ms.left_behind = True
    with pytest.raises(RuntimeError, match="postcondition"):
        _delete()
    assert ms.rolled_back and ms.fired == []
    assert not any("INSERT INTO entity_deletions" in s for _k, s, _p in ms.log)


def test_D10_the_before_commit_seam_runs_inside_the_transaction(env, monkeypatch):
    ms, _ = env
    seen = {}
    monkeypatch.setattr(entity_edit, "_before_commit", lambda: seen.update(committed=ms.committed))
    _delete()
    assert seen == {"committed": False}


def test_D11_no_name_or_rule_key_is_logged_at_any_level(env, caplog):
    ms, _ = env
    with caplog.at_level(logging.DEBUG):
        _delete()
        enr.apply_name_rules  # the block-drop path is covered in test_entity_name_rules
    text = caplog.text.lower()
    for secret in ("total", "gmbh"):
        assert secret not in text


def test_D14_the_request_path_never_touches_qdrant(env):
    ms, vs = env
    _delete()
    assert vs.method_calls == []
    assert [f["event"] for f in ms.fired] == [entity_deletions.Event.ENTITY_DELETED]


def test_a_refused_background_submit_does_not_fail_a_committed_delete(env, monkeypatch, caplog):
    """The real executor raises RuntimeError once shut down; the delete is already committed."""
    ms, _ = env

    def refuse(event, **kw):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(entity_deletions.hooks, "fire_background", refuse)
    with caplog.at_level("WARNING"):
        out = _delete()
    assert out.entity_id == EID
    assert ms.committed
    assert "the sweep will retry" in caplog.text


def test_X1_an_entry_with_nothing_attached_deletes_cleanly(env):
    ms, _ = env
    ms.row = _row(aliases=[])
    ms.visible = dict(ms.row)
    ms.evidence = []
    out = _delete()
    assert out.household_copy_removed is False and out.merges_removed == 0
    assert out.blocked_names == ["Total Gmbh"]


def test_head_checks_precede_any_write(env):
    ms, _ = env
    ms.visible = _row(user_id="bob", scope="shared", published_from=OWNER_ROW)
    with pytest.raises(EntityNotOwner):
        _delete()
    ms.visible = _row(scope="shared", published_from=OWNER_ROW)
    with pytest.raises(EntityNotEditable):
        _delete()
    ms.visible = None
    with pytest.raises(EntityNotFound):
        _delete()
    assert not any(s.startswith(("DELETE", "INSERT")) for _k, s, _p in ms.log)


# ── D13: an explicit name lifts a block ──────────────────────────────────────


def test_D13_rename_adding_a_blocked_name_lifts_the_block(env, monkeypatch):
    ms, _ = env
    lifted = []
    monkeypatch.setattr(enr, "lift_blocks", lambda _ms, **kw: lifted.append(kw) or kw["keys"])
    ms.row = _row(name="Dr. Meier", aliases=[])
    ms.visible = dict(ms.row)
    ms.fetch_one_orig = ms.fetch_one

    def fetch_one(sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("UPDATE entities SET name"):
            ms._note("fetch_one", sql, params)
            name, aliases, *_ = params
            ms.row = {**ms.row, "name": name, "aliases": aliases, "version": 5}
            return dict(ms.row)
        if s.startswith("SELECT entity_id, name, aliases, is_staged"):
            return None  # no collision
        return ms.fetch_one_orig(sql, params)

    ms.fetch_one = fetch_one
    entity_edit.rename_entity(
        _user(), EID, name=None, aliases_add=["Total"], aliases_remove=[], version=4
    )
    assert lifted == [{"user_id": USER, "keys": ["total"], "entity_type": "PERSON"}]

    lifted.clear()
    entity_edit.rename_entity(_user(), EID, name=None, aliases_add=[], aliases_remove=[], version=5)
    assert lifted == []  # nothing new, nothing lifted


# ── hide ─────────────────────────────────────────────────────────────────────


def _shared_row(**over):
    over.setdefault("published_from", OWNER_ROW)
    return _row(user_id="bob", scope="shared", **over)


def test_H1_hide_records_the_owners_source_row(env):
    ms, _ = env
    ms.visible = _shared_row()
    key = entity_edit.hide_entry(_user(), PROJ)
    assert key == OWNER_ROW
    (call,) = [(s, p) for k, s, p in ms.log if s.startswith("INSERT INTO entity_hidden")]
    assert call[1] == (USER, OWNER_ROW)
    assert "ON CONFLICT DO NOTHING" in call[0]


def test_H2_hiding_your_own_entry_is_refused(env):
    ms, _ = env
    with pytest.raises(EntityHideOwn):
        entity_edit.hide_entry(_user(), EID)
    assert not any("entity_hidden" in s for _k, s, _p in ms.log)


def test_H3_an_entry_you_cannot_see_is_not_found(env):
    ms, _ = env
    ms.visible = None
    with pytest.raises(EntityNotFound):
        entity_edit.hide_entry(_user(), EID)
    with pytest.raises(EntityNotFound):
        entity_edit.unhide_entry(_user(), EID)


def test_H4_hide_and_unhide_are_idempotent_single_statements(env):
    ms, _ = env
    ms.visible = _shared_row(published_from=None, entity_id=OWNER_ROW)  # a source-keyed row
    assert entity_edit.hide_entry(_user(), OWNER_ROW) == OWNER_ROW
    assert entity_edit.hide_entry(_user(), OWNER_ROW) == OWNER_ROW
    assert entity_edit.unhide_entry(_user(), OWNER_ROW) is True
    deletes = [p for k, s, p in ms.log if s.startswith("DELETE FROM entity_hidden")]
    assert deletes == [(USER, OWNER_ROW)]


def test_H5_not_hidden_sql_is_the_exact_fragment():
    from visibility import not_hidden_sql

    clause, params = not_hidden_sql("alice", "e")
    assert clause == (
        "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s "
        "AND h.entity_key = coalesce(e.published_from, e.entity_id))"
    )
    assert params == ("alice",)
    assert not_hidden_sql("alice")[0].endswith("coalesce(published_from, entity_id))")
    with pytest.raises(ValueError):
        not_hidden_sql("alice", "e) OR (1=1")


# ── remove_name_rule ─────────────────────────────────────────────────────────


def test_D12_a_removed_block_offers_only_the_callers_own_documents_capped_at_fifty(
    env, monkeypatch
):
    ms, _ = env
    paths = [f"/docs/{i}.pdf" for i in range(60)]
    removed = enr.RemovedBlock(display_name="Total", document_evidence=paths + ["/other/x.pdf"])
    monkeypatch.setattr(enr, "remove_block", lambda _ms, **kw: removed)
    ms.file_rows = [
        {"id": i, "file_path": p} for i, p in enumerate(paths)
    ]  # /other/x.pdf is not hers
    out = entity_edit.remove_name_rule(
        _user(), name_norm="Total", entity_type="person", kind="block"
    )
    assert len(out["reread_documents"]) == 50
    assert out["reread_more"] == 10
    assert out["reread_documents"][0] == {"document_id": 0, "title": "0.pdf"}
    (call,) = [(s, p) for k, s, p in ms.log if "FROM file_index" in s]
    assert call[1][0] == USER and "published_from IS NULL" in call[0]


def test_remove_name_rule_normalises_and_reports_restored_corrections(env, monkeypatch):
    ms, _ = env
    seen = {}

    def fake_remove(_ms, **kw):
        seen.update(kw)
        return enr.RemovedBlock("Total", [], [("ORG", "PERSON")])

    monkeypatch.setattr(enr, "remove_block", fake_remove)
    out = entity_edit.remove_name_rule(
        _user(), name_norm="  TOTAL  Gmbh ", entity_type=" person ", kind="block"
    )
    assert seen["name_norm"] == "total gmbh" and seen["entity_type"] == "PERSON"
    assert out["restored_corrections"] == [
        {
            "name_norm": "total gmbh",
            "display_name": "Total",
            "from_type": "ORG",
            "to_type": "PERSON",
        }
    ]
    assert out["reread_documents"] == []


def test_remove_name_rule_unknown_is_not_found(env, monkeypatch):
    monkeypatch.setattr(enr, "remove_block", lambda _ms, **kw: None)
    monkeypatch.setattr(enr, "remove_retype", lambda _ms, **kw: False)
    with pytest.raises(NameRuleNotFound):
        entity_edit.remove_name_rule(_user(), name_norm="x", entity_type="PERSON", kind="block")
    with pytest.raises(NameRuleNotFound):
        entity_edit.remove_name_rule(_user(), name_norm="x", entity_type="PERSON", kind="retype")


def test_list_name_rules_shapes_the_three_lists(env, monkeypatch):
    ms, _ = env
    monkeypatch.setattr(
        enr,
        "list_rules",
        lambda _ms, uid, limit=500: {
            "blocks": [
                {
                    "name_norm": "total",
                    "from_type": "PERSON",
                    "display_name": None,
                    "created_at": "2026-11-17T00:00:00Z",
                    "document_count": 3,
                }
            ],
            "corrections": [
                {
                    "name_norm": "acme",
                    "from_type": "PERSON",
                    "to_type": "ORG",
                    "display_name": "Acme",
                }
            ],
            "blocked_total": 501,
            "corrections_total": 1,
        },
    )
    ms.visible_hidden = [
        {
            "entity_id": PROJ,
            "name": "Bob's note",
            "entity_type": "CONCEPT",
            "hidden_at": "2026-11-17T00:00:00Z",
        }
    ]
    out = entity_edit.list_name_rules(_user())
    assert out["blocked"][0]["display_name"] == "total"  # pre-084 rows fall back to the key
    assert out["blocked"][0]["document_count"] == 3
    assert out["corrections"][0]["to_type"] == "ORG"
    assert out["hidden"][0]["type"] == "CONCEPT"
    assert out["blocked_total"] == 501 and out["hidden_total"] == 1
