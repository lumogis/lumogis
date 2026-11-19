# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the member merge/undo/history/move path in ``services/entity_edit`` (LUM-965).

Real SQL, ownership and isolation are in ``tests/integration/test_entity_merge_undo_live.py``;
this file pins what is cheap to pin with a fake store: argument mapping, the
store-outage mapping and the history ordering rule.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import MagicMock
from unittest.mock import patch

import psycopg2
import pytest
from auth import UserContext

from services import entity_edit

USER = UserContext(user_id="alice", is_authenticated=True)
A, B = str(uuid.uuid4()), str(uuid.uuid4())
T0 = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def _row(eid, **kw):
    base = {
        "entity_id": eid,
        "name": "N",
        "entity_type": "PERSON",
        "aliases": [],
        "version": 1,
        "user_id": "alice",
        "scope": "personal",
        "published_from": None,
        "is_staged": False,
    }
    base.update(kw)
    return base


@contextmanager
def _store(fetch_one=None, fetch_all=None):
    ms = MagicMock()
    ms.fetch_one.side_effect = fetch_one or (lambda sql, params=None: _row(params[0]))
    ms.fetch_all.side_effect = fetch_all or (lambda sql, params=None: [])
    with (
        patch("services.entity_edit.config.get_metadata_store", return_value=ms),
        patch(
            "services.entity_edit._resolve_own_personal_row",
            side_effect=lambda user, eid: _row(eid),
        ),
    ):
        yield ms


def test_M4_keep_name_and_origin_are_mapped_to_the_engine():
    outcome = MagicMock(winner_row=_row(A), merge_id="m-1")
    for keep, expected in (("this", "winner"), ("other", "loser")):
        with (
            _store(),
            patch("services.entity_merge.merge_in_transaction", return_value=outcome) as eng,
            patch("services.entity_merge.after_merge_commit") as post,
        ):
            row, merge_id = entity_edit.merge_entries(
                USER, A, B, version=1, other_version=1, keep_name=keep
            )
        assert merge_id == "m-1" and row["entity_id"] == A
        kw = eng.call_args.kwargs
        assert (kw["keep_name"], kw["origin"], kw["acted_by"]) == (expected, "member", "alice")
        assert (kw["winner_id"], kw["loser_id"]) == (A, B)
        post.assert_called_once_with(outcome)


def test_M3_same_entry_is_refused_before_any_write():
    with _store() as ms, pytest.raises(entity_edit.EntitySameEntry):
        entity_edit.merge_entries(USER, A, A, version=1, other_version=1)
    ms.transaction.assert_not_called()


def test_M8_store_errors_are_unavailable():
    outcome = MagicMock()
    with (
        _store(),
        patch(
            "services.entity_merge.merge_in_transaction",
            side_effect=psycopg2.OperationalError("down"),
        ),
        pytest.raises(entity_edit.EntityStoreUnavailable),
    ):
        entity_edit.merge_entries(USER, A, B, version=1, other_version=1)
    del outcome
    with (
        _store(),
        patch(
            "services.entity_merge.undo_in_transaction",
            side_effect=psycopg2.OperationalError("down"),
        ),
        pytest.raises(entity_edit.EntityStoreUnavailable),
    ):
        entity_edit.undo_merge(USER, str(uuid.uuid4()))


def test_M5_malformed_merge_id_never_reaches_the_store():
    with _store() as ms, pytest.raises(entity_edit.MergeNotFound):
        entity_edit.undo_merge(USER, "not-a-uuid")
    ms.transaction.assert_not_called()


def test_M7_move_refuses_foreign_ids_via_the_engine():
    with (
        _store(),
        patch(
            "services.entity_merge.move_mentions_in_transaction",
            side_effect=entity_edit.MentionsNotFound(),
        ),
        pytest.raises(entity_edit.MentionsNotFound),
    ):
        entity_edit.move_mentions(USER, A, B, relation_ids=[1], version=1, to_version=1)


def _hist(mid, winner, loser, minutes, undone=False, sharing=False):
    return {
        "merge_id": mid,
        "winner_id": winner,
        "loser_id": loser,
        "origin": "member",
        "merged_at": T0 + timedelta(minutes=minutes),
        "undone_at": T0 if undone else None,
        "loser_name": f"name-{mid}",
        "winner_name": "Winner",
        "restores_sharing": sharing,
    }


def test_M10_history_marks_only_the_newest_open_merge_undoable():
    rows = [
        _hist("m3", A, "x3", 30, sharing=True),
        _hist("m2", A, "x2", 20),
        _hist("m1", A, "x1", 10, undone=True),
        _hist("m0", "other", A, 5, undone=True),
    ]
    with _store(fetch_all=lambda sql, params=None: rows):
        items = entity_edit.list_merge_history(USER, A)
    by = {i["merge_id"]: i for i in items}
    assert by["m3"]["can_undo"] is True and by["m3"]["newer_merge_id"] is None
    assert by["m3"]["restores_sharing"] is True
    assert by["m2"]["can_undo"] is False and by["m2"]["newer_merge_id"] == "m3"
    assert by["m2"]["newer_other_name"] == "name-m3"
    assert by["m1"]["can_undo"] is False and by["m1"]["newer_merge_id"] is None
    assert by["m0"]["direction"] == "merged_out" and by["m0"]["other_name"] == "Winner"
    assert by["m0"]["other_entity_id"] == "other"
