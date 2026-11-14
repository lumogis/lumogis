# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-645 — systematic opt-in mailbox organization service (email_organize).

Unit-level coverage of the security/correctness-relevant logic with an in-memory
fake store + a recording adapter: which ops fire for which opt-in, label-before-move
ordering, folder-sort per-category (+ never 'other'), ledger idempotency, undo
move-before-label ordering, control-char label rejection, and deleted-slug skip.
"""

from __future__ import annotations

import pytest

from services import email_organize as eo

_URL = "imap://11111111-1111-1111-1111-111111111111/100/5"
_URL2 = "imap://11111111-1111-1111-1111-111111111111/100/6"
_SID = "11111111-1111-1111-1111-111111111111"


class _RecordingAdapter:
    def __init__(self):
        self.calls = []
        self.reversed = []
        self.sessions = 0

    def organize_session(self):
        self.sessions += 1
        return object()  # opaque session token

    def close_session(self, client):
        pass

    def apply_organize(self, *, uidvalidity, uid, op, label, slug="", mailbox="INBOX", client=None):
        self.calls.append(op)
        target = f"Lumogis/{label}"
        kind = "gmail" if op == "label" else None
        return {
            "op": op,
            "mailbox": mailbox,
            "message_id": f"<mid-{uid}>",
            "target": target,
            "label_kind": kind,
        }

    def reverse_writeback(self, *, op, mailbox, message_id, target=None, flag=None, client=None):
        self.reversed.append((op, target))


class _FakeStore:
    def __init__(self, *, opt_in, signals=(), undo_scope=None):
        self._opt_in = opt_in  # dict for the sources opt-in row
        self._signals = list(signals)  # [{"url","category"}]
        self._ledger = []  # active rows keyed by signal_url/op/target/token/id
        self._reversed = []
        self._undo_scope = undo_scope
        self._next_id = 1

    def fetch_one(self, q, params=None):
        if "organize_label_enabled" in q:
            return dict(self._opt_in)
        # VERIFY-PLAN: updated to match implementation. LUM-729's FP-076 bound
        # reads the tick counter in the SAME round trip as the scope, so the
        # literal "organize_undo_scope FROM sources" no longer appears — the
        # column list sits between them. Matching on the scope column alone keeps
        # this fake honest without pinning it to a column order.
        if "organize_undo_scope" in q and "FROM sources" in q:
            return {"organize_undo_scope": self._undo_scope, "ticks": 0}
        if "FROM email_organize_log" in q and "reversed_at IS NULL LIMIT 1" in q:
            url, op = params[0], params[1]
            active = any(r["signal_url"] == url and r["op"] == op for r in self._ledger)
            return {"1": 1} if active else None
        if "FROM email_organize_log WHERE source_id" in q and "LIMIT 1" in q:
            ops = set(params[1])
            return {"1": 1} if any(r["op"] in ops for r in self._ledger) else None
        return None

    def fetch_all(self, q, params=None):
        if "LEFT JOIN email_organize_log" in q:
            op = params[0]
            done = {r["signal_url"] for r in self._ledger if r["op"] == op}
            rows = [s for s in self._signals if s["url"] not in done]
            # move query is category-scoped in SQL (params: op, sid, since, cats, limit).
            if "s.category = ANY(%s)" in q and len(params) == 5:
                cats = set(params[3])
                rows = [s for s in rows if s["category"] in cats]
            return rows
        if "SELECT id, signal_url, op, reverse_token" in q:
            ops = set(params[1])
            rows = [r for r in self._ledger if r["op"] in ops]
            # mirror the ORDER BY (op='move') DESC, id ASC
            return sorted(rows, key=lambda r: (r["op"] != "move", r["id"]))
        return []

    def execute(self, q, params=None):
        if "INSERT INTO email_organize_log" in q:
            self._ledger.append(
                {
                    "id": self._next_id,
                    "signal_url": params[2],
                    "op": params[3],
                    "target": params[4],
                    "reverse_token": _loads(params[6]),
                }
            )
            self._next_id += 1
        elif "UPDATE email_organize_log SET reversed_at" in q:
            rid = params[0]
            self._ledger = [r for r in self._ledger if r["id"] != rid]
        elif "organize_undo_scope = NULL" in q:
            self._undo_scope = None


def _loads(s):
    import json

    return json.loads(s) if isinstance(s, str) else s


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(
        eo,
        "_label_for",
        lambda uid, slug: {
            "bills_finance": "Bills & finance",
            "promotional_bulk": "Promotional / bulk",
            "other": "Other",
        }.get(slug),
    )
    eo._opt_in_cache.clear()


def _wire(store, adapter, monkeypatch):
    monkeypatch.setattr(eo.config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(eo, "_adapter_for", lambda sid, uid: adapter)


def test_label_only_applies_label(monkeypatch):
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": True,
            "organize_folder_enabled": False,
            "organize_folder_categories": [],
            "organize_since": "x",
        }
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="bills_finance", user_id="u")
    assert adapter.calls == ["label"]
    assert store._ledger[0]["op"] == "label"


def test_folder_sort_only_selected_and_label_before_move(monkeypatch):
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": True,
            "organize_folder_enabled": True,
            "organize_folder_categories": ["promotional_bulk"],
            "organize_since": "x",
        }
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    # selected category → label THEN move (ordering)
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="promotional_bulk", user_id="u")
    assert adapter.calls == ["label", "move"]
    # non-selected category → label only (no move)
    adapter.calls.clear()
    eo.organize_signal(signal_url=_URL2, source_id=_SID, category="bills_finance", user_id="u")
    assert adapter.calls == ["label"]


def test_folder_sort_never_moves_other(monkeypatch):
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": False,
            "organize_folder_enabled": True,
            "organize_folder_categories": ["other"],  # stripped by cache filter
            "organize_since": "x",
        }
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="other", user_id="u")
    assert adapter.calls == []  # 'other' never sorted


def test_idempotent_no_double_apply(monkeypatch):
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": True,
            "organize_folder_enabled": False,
            "organize_folder_categories": [],
            "organize_since": "x",
        }
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="bills_finance", user_id="u")
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="bills_finance", user_id="u")
    assert adapter.calls == ["label"]  # second is a no-op (active ledger row)
    assert len(store._ledger) == 1


def test_deleted_custom_slug_skipped(monkeypatch):
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": True,
            "organize_folder_enabled": False,
            "organize_folder_categories": [],
            "organize_since": "x",
        }
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="gone_slug", user_id="u")
    assert adapter.calls == []  # label_map has no entry → skip, no KeyError


def test_sanitize_rejects_control_chars():
    with pytest.raises(Exception):
        eo._sanitize_artifact_name("Bills\r\nInjection")
    assert eo._sanitize_artifact_name("Bills & finance") == "Bills & finance"


def test_reconcile_move_is_category_scoped_no_starvation(monkeypatch):
    # Out-of-scope categories must NOT be selected (they'd fill the window and starve
    # the in-scope ones). Only the selected category is moved.
    signals = [
        {"url": "imap://s/100/1", "category": "bills_finance"},  # not selected
        {"url": "imap://s/100/2", "category": "promotional_bulk"},  # selected
    ]
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": False,
            "organize_folder_enabled": True,
            "organize_folder_categories": ["promotional_bulk"],
            "organize_since": "epoch",
        },
        signals=signals,
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    out = eo.reconcile_tick(source_id=_SID, user_id="u")
    assert out["moved"] == 1  # only promotional_bulk
    assert [r["signal_url"] for r in store._ledger] == ["imap://s/100/2"]


def test_reconcile_reuses_one_session_for_the_batch(monkeypatch):
    signals = [
        {"url": "imap://s/100/1", "category": "bills_finance"},
        {"url": "imap://s/100/2", "category": "bills_finance"},
    ]
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": True,
            "organize_folder_enabled": False,
            "organize_folder_categories": [],
            "organize_since": "epoch",
        },
        signals=signals,
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    eo.reconcile_tick(source_id=_SID, user_id="u")
    # Both messages labeled through a SINGLE opened session (no per-message connect churn).
    assert adapter.calls == ["label", "label"]
    assert adapter.sessions == 1


def test_undo_reverses_move_before_label(monkeypatch):
    store = _FakeStore(
        opt_in={
            "organize_label_enabled": True,
            "organize_folder_enabled": True,
            "organize_folder_categories": ["promotional_bulk"],
            "organize_since": "x",
        },
        undo_scope="all",
    )
    adapter = _RecordingAdapter()
    _wire(store, adapter, monkeypatch)
    # seed a label+move pair
    eo.organize_signal(signal_url=_URL, source_id=_SID, category="promotional_bulk", user_id="u")
    assert {r["op"] for r in store._ledger} == {"label", "move"}
    out = eo.undo_tick(source_id=_SID, user_id="u")
    assert out["reversed"] == 2
    assert [op for op, _ in adapter.reversed] == ["move", "label"]  # move BEFORE label
    assert store._ledger == []  # all cleared


def test_opt_in_bypass_cache_reads_db_when_stale(monkeypatch):
    """Feed poller must not trust a stale enabled cache after undo disables opt-in."""
    import time

    store = _FakeStore(
        opt_in={
            "organize_label_enabled": False,
            "organize_folder_enabled": False,
            "organize_folder_categories": [],
            "organize_since": "x",
        }
    )
    monkeypatch.setattr(eo.config, "get_metadata_store", lambda: store)
    eo._opt_in_cache[_SID] = (
        (True, True, frozenset(), "x"),
        time.monotonic() + 60.0,
    )
    cached = eo._opt_in_for(_SID)
    assert cached[0] is True  # stale cache hit
    fresh = eo._opt_in_for(_SID, bypass_cache=True)
    assert fresh[0] is False
    assert fresh[1] is False
