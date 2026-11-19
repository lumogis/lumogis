# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The tombstone, the Core Qdrant listener and the retry sweep after a delete (LUM-966)."""

from __future__ import annotations

import logging

import pytest

import config as _config
from services import entity_deletions as ed

EID = "11111111-1111-4111-9111-111111111111"
PROJ = "22222222-2222-4222-9222-222222222222"
USER = "alice"


class Store:
    def __init__(self, pending=None, done=None, fail=False):
        self.pending = pending or []
        self.done = done or []
        self.fail = fail
        self.log: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), tuple(params or ())))

    def fetch_all(self, sql, params=None):
        if self.fail:
            raise RuntimeError("store down")
        s = " ".join(sql.split())
        self.log.append((s, tuple(params or ())))
        if "points_cleared_at IS NOT NULL AND graph_cleared_at IS NOT NULL" in s:
            return list(self.done)
        return list(self.pending)

    def statements(self, prefix):
        return [(s, p) for s, p in self.log if s.startswith(prefix)]


class Vectors:
    """``count_where`` answers from a script (one count per filter call)."""

    def __init__(self, counts=(0, 0), delete_fails=0):
        self.counts = list(counts)
        self.delete_fails = delete_fails
        self.deleted: list[dict] = []
        self.counted: list[dict] = []

    def delete_where(self, collection, filter):
        if self.delete_fails:
            self.delete_fails -= 1
            raise RuntimeError("qdrant down")
        self.deleted.append(filter)

    def count_where(self, collection, filter):
        self.counted.append(filter)
        return self.counts.pop(0) if self.counts else 0


@pytest.fixture
def env(monkeypatch):
    ms, vs = Store(), Vectors()
    _config._instances["metadata_store"] = ms
    _config._instances["vector_store"] = vs
    fired: list[dict] = []
    monkeypatch.setattr(
        ed.hooks, "fire_background", lambda event, **kw: fired.append({"event": event, **kw})
    )
    monkeypatch.setattr("services.memory_purge._RETRY_BACKOFF_S", 0)
    ms.fired = fired
    yield ms, vs
    _config._instances.clear()


def _t():
    return ed.Tombstone(entity_id=EID, user_id=USER, projection_ids=[PROJ])


def _row(**over):
    row = {
        "entity_id": EID,
        "user_id": USER,
        "projection_ids": [PROJ],
        "points_cleared_at": None,
        "graph_cleared_at": None,
        "attempts": 0,
    }
    row.update(over)
    return row


# ── clear_points ─────────────────────────────────────────────────────────────


def test_S1_clear_points_deletes_counts_both_filters_and_stamps_only_when_zero(env):
    ms, vs = env
    assert ed.clear_points(_t()) is True
    keys = [{c["key"] for c in f["must"]} for f in vs.deleted]
    assert keys == [{"user_id", "entity_id"}, {"user_id", "published_from"}]
    assert len(vs.counted) == 2  # both filters verified
    (stamp,) = ms.statements("UPDATE entity_deletions SET points_cleared_at = NOW()")
    assert stamp[1] == (EID, USER)


def test_S2_points_still_there_means_no_stamp_and_an_error_log(env, caplog):
    ms, vs = env
    vs.counts = [0, 2]
    with caplog.at_level(logging.ERROR, logger="services.entity_deletions"):
        assert ed.clear_points(_t()) is False
    assert ms.statements("UPDATE entity_deletions") == []
    assert "points not cleared, sweep will retry" in caplog.text


def test_S2b_a_failing_qdrant_delete_never_raises_and_never_stamps(env):
    ms, vs = env
    vs.delete_fails = 99
    assert ed.clear_points(_t()) is False
    assert ms.statements("UPDATE entity_deletions") == []


def test_S8_the_core_listener_rebuilds_the_tombstone_from_ids(env):
    ms, vs = env
    ed.on_entity_deleted_points(entity_id=EID, user_id=USER, projection_ids=[PROJ], unknown="x")
    assert ms.statements("UPDATE entity_deletions SET points_cleared_at")[0][1] == (EID, USER)


def test_fire_deleted_carries_ids_only(env):
    ms, _ = env
    ed.after_delete_commit(_t())
    (e,) = ms.fired
    assert e == {
        "event": ed.Event.ENTITY_DELETED,
        "entity_id": EID,
        "user_id": USER,
        "projection_ids": [PROJ],
    }


# ── tombstone insert ─────────────────────────────────────────────────────────


def test_S3_graph_disabled_stamps_the_graph_arm_at_insert(env):
    ms, _ = env
    ed.insert_tombstone(ms, _t(), graph_enabled=False)
    ed.insert_tombstone(ms, _t(), graph_enabled=True)
    (off_sql, off_params), (on_sql, on_params) = ms.statements("INSERT INTO entity_deletions")
    assert off_params == (EID, USER, [PROJ], False)
    assert on_params == (EID, USER, [PROJ], True)
    assert "CASE WHEN %s THEN NULL ELSE NOW() END" in off_sql and "%s::uuid[]" in off_sql


# ── the sweep ────────────────────────────────────────────────────────────────


def test_S4_the_sweep_refires_unstamped_graph_arms_and_counts_attempts(env):
    ms, vs = env
    ms.pending = [_row(points_cleared_at="t", attempts=2)]
    out = ed.sweep_entity_deletions()
    assert out["graph"] == 1
    assert ms.statements("UPDATE entity_deletions SET attempts = attempts + 1")[0][1] == (EID, USER)
    assert [e["event"] for e in ms.fired] == [ed.Event.ENTITY_DELETED]
    assert ms.statements("UPDATE entity_deletions SET graph_cleared_at") == []


def test_S4b_the_select_leaves_fresh_tombstones_alone(env):
    ms, _ = env
    ed.sweep_entity_deletions()
    (sel, params) = ms.statements(
        "SELECT entity_id, user_id, projection_ids::text[] AS projection_ids, points_cleared_at"
    )[0]
    assert "deleted_at < now() - interval '2 minutes'" in sel
    assert params == (100,)


def test_S7_at_the_tenth_attempt_the_sweep_gives_up_and_stamps_the_graph_arm(env, caplog):
    ms, _ = env
    ms.pending = [_row(points_cleared_at="t", attempts=9)]
    with caplog.at_level(logging.ERROR, logger="services.entity_deletions"):
        out = ed.sweep_entity_deletions()
    assert out["gave_up"] == 1
    assert "left to the orphan GC" in caplog.text
    assert ms.statements("UPDATE entity_deletions SET graph_cleared_at = NOW()")[0][1] == (
        EID,
        USER,
    )
    assert len(ms.fired) == 1  # the last attempt still fires once


def test_S7b_rows_past_the_budget_are_not_refired(env):
    ms, _ = env
    ms.pending = [_row(points_cleared_at="t", attempts=10)]
    ed.sweep_entity_deletions()
    assert ms.fired == []


def test_S4c_the_sweep_retries_unstamped_points_through_clear_points(env):
    ms, vs = env
    ms.pending = [_row(graph_cleared_at="t")]
    out = ed.sweep_entity_deletions()
    assert out["points"] == 1
    assert ms.fired == []  # the graph arm was already done


def test_S5_s8_old_cleared_tombstones_are_removed_only_when_qdrant_is_empty(env):
    ms, vs = env
    ms.done = [_row(points_cleared_at="t", graph_cleared_at="t")]
    out = ed.sweep_entity_deletions()
    assert out["removed"] == 1
    assert ms.statements("DELETE FROM entity_deletions")[0][1] == (EID, USER)

    ms.log.clear()
    vs.counts = [0, 1]  # a publish landed a point after the first clear
    out = ed.sweep_entity_deletions()
    assert out["recheck"] == 1 and out["removed"] == 0
    assert ms.statements("DELETE FROM entity_deletions") == []
    assert ms.statements("UPDATE entity_deletions SET points_cleared_at = NULL")


def test_S5b_the_done_select_keeps_young_rows(env):
    ms, _ = env
    ed.sweep_entity_deletions()
    sels = [
        s
        for s, _p in ms.log
        if "points_cleared_at IS NOT NULL AND graph_cleared_at IS NOT NULL" in s
    ]
    assert "deleted_at < now() - interval '7 days'" in sels[0]


def test_S6_the_sweep_never_raises(env, caplog):
    ms, _ = env
    ms.fail = True
    with caplog.at_level(logging.ERROR, logger="services.entity_deletions"):
        out = ed.sweep_entity_deletions()
    assert out == {"points": 0, "graph": 0, "gave_up": 0, "removed": 0, "recheck": 0}
    assert "entity_deletion_sweep failed" in caplog.text


def test_E5_main_registers_the_core_listener_in_every_graph_mode():
    """The listener is registered next to the sweep, outside any graph-mode branch."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text()
    reg = src.index(
        "hooks.register(Event.ENTITY_DELETED, entity_deletions.on_entity_deleted_points)"
    )
    preceding = src[:reg].rsplit("\n\n", 1)[-1]
    assert not re.search(r"graph_mode|GRAPH_MODE", preceding)
    assert 'id="entity_deletion_sweep"' in src
