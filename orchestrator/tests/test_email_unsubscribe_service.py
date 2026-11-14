# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-sender persistence, suppression, and the ingest-path wiring (LUM-697).

The two load-bearing behaviours here are (a) the upsert can only ever *add* or
*refresh* an unsubscribe target, never erase one, and (b) a suppressed message
short-circuits **before** the on-box LLM classification rather than merely
before the DB write.
"""

from __future__ import annotations

import pytest
from models.signals import Signal

import config
from services import email_sender_rules
from services import email_unsubscribe

_UID = "user-A"
_SRC = "11111111-1111-1111-1111-111111111111"


class _Store:
    """Minimal fake modelling the one row per (user, source, sender) contract,
    including the preserve-on-empty UPDATE."""

    def __init__(self):
        self.rows: dict[tuple[str, str, str], dict] = {}
        self.rules: list[dict] = []

    # -- writes -------------------------------------------------------------
    def execute(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        p = params or ()
        if "insert into email_unsubscribe_target" in ql:
            key = (p[0], p[1], p[2])
            new = {
                "from_display": p[3],
                "mailto_addr": p[4],
                "mailto_subject": p[5],
                "https_uri": p[6],
                "one_click": p[7],
                "message_count": 1,
                "unsubscribed_at": None,
            }
            cur = self.rows.get(key)
            if cur is None:
                self.rows[key] = new
                return
            # COALESCE(NULLIF(EXCLUDED.x, ''), existing.x)
            for col in ("mailto_addr", "mailto_subject", "https_uri", "from_display"):
                if new[col]:
                    cur[col] = new[col]
            cur["one_click"] = bool(cur["one_click"]) or bool(new["one_click"])
            cur["message_count"] += 1
            return
        if "update email_unsubscribe_target set unsubscribed_at" in ql:
            row = self.rows.get((p[0], p[1], p[2]))
            if row is not None:
                row["unsubscribed_at"] = "now"
            return
        if "insert into email_sender_rule" in ql:
            self.rules = [
                r
                for r in self.rules
                if not (r["user_id"] == p[0] and r["source_id"] == p[1] and r["from_addr"] == p[2])
            ]
            self.rules.append(
                {
                    "user_id": p[0],
                    "source_id": p[1],
                    "from_addr": p[2],
                    "action": p[3],
                    "active": True,
                }
            )
            return

    # -- reads --------------------------------------------------------------
    def fetch_one(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        p = params or ()
        if "select 1 as ok from email_unsubscribe_target" in ql:
            return {"ok": 1} if (p[0], p[1], p[2]) in self.rows else None
        if "select mailto_addr, mailto_subject from email_unsubscribe_target" in ql:
            return self.rows.get((p[0], p[1], p[2]))
        if "select unsubscribed_at from email_unsubscribe_target" in ql:
            return self.rows.get((p[0], p[1], p[2]))
        return None

    def fetch_all(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        p = params or ()
        if "from email_sender_rule where user_id = %s and active" in ql:
            return [r for r in self.rules if r["user_id"] == p[0] and r["active"]]
        if "delete from email_sender_rule" in ql:
            before = len(self.rules)
            self.rules = [
                r
                for r in self.rules
                if not (r["user_id"] == p[0] and r["source_id"] == p[1] and r["from_addr"] == p[2])
            ]
            return [{"id": 1}] * (before - len(self.rules))
        return []


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    email_sender_rules.invalidate_cache()
    yield s
    email_sender_rules.invalidate_cache()


def _record(store, **over):
    kw = {
        "user_id": _UID,
        "source_id": _SRC,
        "from_addr": "news@retailer.example",
        "from_display": "Retailer",
        "headers": {"List-Unsubscribe": "<mailto:u@retailer.example>"},
    }
    kw.update(over)
    email_unsubscribe.record_from_headers(**kw)


# --------------------------------------------------------------------------- #
# upsert semantics
# --------------------------------------------------------------------------- #
def test_upsert_increments_count_rather_than_duplicating(store):
    _record(store)
    _record(store)
    assert len(store.rows) == 1
    assert next(iter(store.rows.values()))["message_count"] == 2


def test_upsert_is_scoped_per_source(store):
    _record(store)
    _record(store, source_id="22222222-2222-2222-2222-222222222222")
    assert len(store.rows) == 2


def test_later_message_without_the_header_does_not_blank_the_target(store):
    """A retailer's newsletter carries List-Unsubscribe; its order confirmations
    do not. A plain `= EXCLUDED.x` would erase a working target on the second
    message and the sender would silently become un-unsubscribable."""
    _record(store)
    _record(store, headers={"Subject": "Your order shipped"})
    row = next(iter(store.rows.values()))
    assert row["mailto_addr"] == "u@retailer.example"
    assert row["message_count"] == 2


def test_later_message_does_refresh_a_changed_target(store):
    _record(store)
    _record(store, headers={"List-Unsubscribe": "<mailto:new@retailer.example>"})
    assert next(iter(store.rows.values()))["mailto_addr"] == "new@retailer.example"


def test_empty_from_addr_writes_no_row(store):
    """_extract_sender returns "" for an unparseable From. Without the guard
    every such sender collapses into one '' row with a runaway count and an
    unsubscribe target belonging to whichever message wrote last."""
    _record(store, from_addr="")
    _record(store, from_addr="   ")
    assert store.rows == {}


def test_ordinary_email_without_the_header_writes_no_row(store):
    _record(store, headers={"Subject": "lunch?"})
    assert store.rows == {}


def test_record_never_raises_on_store_error(monkeypatch):
    class _Boom:
        def fetch_one(self, *a, **k):
            raise RuntimeError("db down")

        def execute(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Boom())
    email_unsubscribe.record_from_headers(
        user_id=_UID,
        source_id=_SRC,
        from_addr="a@x.com",
        from_display="",
        headers={"List-Unsubscribe": "<mailto:u@x.com>"},
    )  # must not raise


# --------------------------------------------------------------------------- #
# server-side resolution
# --------------------------------------------------------------------------- #
def test_resolve_targets_returns_stored_recipients_and_skips_the_rest(store):
    _record(store)
    _record(store, from_addr="plain@x.com", headers={"List-Unsubscribe": "<https://x.com/u>"})
    resolved, skipped = email_unsubscribe.resolve_targets(
        _UID, _SRC, ["news@retailer.example", "plain@x.com"]
    )
    assert [r["from_addr"] for r in resolved] == ["news@retailer.example"]
    assert resolved[0]["mailto_addr"] == "u@retailer.example"
    assert skipped == ["plain@x.com"]


def test_resolve_targets_defaults_the_subject(store):
    _record(store)
    resolved, _ = email_unsubscribe.resolve_targets(_UID, _SRC, ["news@retailer.example"])
    assert resolved[0]["subject"] == "unsubscribe"


def test_already_unsubscribed_tracks_the_stamp(store):
    _record(store)
    assert not email_unsubscribe.already_unsubscribed(_UID, _SRC, "news@retailer.example")
    email_unsubscribe.mark_unsubscribed(_UID, _SRC, "news@retailer.example")
    assert email_unsubscribe.already_unsubscribed(_UID, _SRC, "news@retailer.example")


# --------------------------------------------------------------------------- #
# suppression rules
# --------------------------------------------------------------------------- #
def test_should_suppress_reflects_a_set_rule(store):
    assert not email_sender_rules.should_suppress(_UID, _SRC, "news@retailer.example")
    email_sender_rules.set_rule(_UID, _SRC, "news@retailer.example")
    assert email_sender_rules.should_suppress(_UID, _SRC, "news@retailer.example")


def test_rule_cache_is_invalidated_on_write_not_only_by_ttl(store):
    email_sender_rules.should_suppress(_UID, _SRC, "news@retailer.example")  # warm the cache
    email_sender_rules.set_rule(_UID, _SRC, "news@retailer.example")
    assert email_sender_rules.should_suppress(_UID, _SRC, "news@retailer.example")


def test_clearing_a_rule_takes_effect_immediately(store):
    email_sender_rules.set_rule(_UID, _SRC, "news@retailer.example")
    assert email_sender_rules.clear_rule(_UID, _SRC, "news@retailer.example") == 1
    assert not email_sender_rules.should_suppress(_UID, _SRC, "news@retailer.example")


def test_rules_are_matched_case_insensitively(store):
    email_sender_rules.set_rule(_UID, _SRC, "News@Retailer.Example")
    assert email_sender_rules.should_suppress(_UID, _SRC, "news@retailer.example")


def test_only_hide_is_an_accepted_rule_action(store):
    for bad in ("mark_read", "trash", "delete_everything"):
        with pytest.raises(ValueError):
            email_sender_rules.set_rule(_UID, _SRC, "a@x.com", bad)


def test_should_suppress_fails_open_on_store_error(monkeypatch):
    class _Boom:
        def fetch_all(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Boom())
    email_sender_rules.invalidate_cache()
    # Fail OPEN — a store blip must show the message, never hide it.
    assert email_sender_rules.should_suppress(_UID, _SRC, "a@x.com") is False


def test_a_suppressed_sender_is_still_listed_so_it_can_be_un_silenced(store):
    _record(store)
    email_sender_rules.set_rule(_UID, _SRC, "news@retailer.example")
    _record(store)  # record runs BEFORE the suppression check
    assert next(iter(store.rows.values()))["message_count"] == 2


# --------------------------------------------------------------------------- #
# ingest wiring — the reason the check sits above the LLM branch
# --------------------------------------------------------------------------- #
def _email_signal():
    return Signal(
        signal_id="s1",
        source_id=_SRC,
        title="Sale!",
        url=f"imap://{_SRC}/1/9",
        published_at=None,
        content_summary="",
        raw_content="body",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=None,
        user_id=_UID,
        source_type="imap",
        headers={
            "From": "News <news@retailer.example>",
            "List-Unsubscribe": "<mailto:u@retailer.example>",
        },
    )


def test_suppressed_message_makes_no_llm_call_and_is_never_persisted(store, monkeypatch):
    """The whole point of hoisting the check above the LLM branch. Without this
    test a later refactor silently reintroduces a full on-box classification for
    every message from a sender the user explicitly silenced."""
    from services import signal_processor

    calls = {"llm": 0, "persist": 0}
    monkeypatch.setattr(
        signal_processor,
        "_call_llm",
        lambda *a, **k: calls.__setitem__("llm", calls["llm"] + 1) or {},
    )
    monkeypatch.setattr(
        signal_processor,
        "_persist",
        lambda *a, **k: calls.__setitem__("persist", calls["persist"] + 1),
    )
    email_sender_rules.set_rule(_UID, _SRC, "news@retailer.example")

    out = signal_processor.process_signal(_email_signal(), user_id=_UID)

    assert calls["llm"] == 0, "a suppressed message must not reach the on-box model"
    assert calls["persist"] == 0
    assert out is not None and out.raw_content == ""


def test_unsuppressed_message_still_reaches_the_llm(store, monkeypatch):
    from services import signal_processor

    calls = {"llm": 0}
    monkeypatch.setattr(
        signal_processor,
        "_call_llm",
        lambda *a, **k: calls.__setitem__("llm", calls["llm"] + 1) or {},
    )
    monkeypatch.setattr(signal_processor, "_persist", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.email_classifier.require_local_model", lambda uid, *, task: "llama"
    )
    signal_processor.process_signal(_email_signal(), user_id=_UID)
    assert calls["llm"] == 1
