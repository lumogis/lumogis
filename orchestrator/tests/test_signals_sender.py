# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-642 — persist the email sender (from_addr + from_display) on signals.

The /email surface renders the sender richly. The classifier already parses the
From header (for known-contact detection) but discarded it; these tests prove
process_signal now persists both the bare address and the display name for email
signals, and — critically — that a NON-email signal does not raise (the sender
locals exist only inside the ``if is_email`` branch; a naive assignment would
``NameError`` on the shared Signal build).
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from models.signals import Signal

from services import signal_processor as sp


def _signal(**kw) -> Signal:
    base = dict(
        signal_id="sig1",
        source_id="src-abc",
        title="Dentist appointment confirmation",
        url="imap://src-abc/100/5",
        published_at=None,
        content_summary="",
        raw_content="",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id="u1",
        source_type="imap",
        category="",
        headers={"From": "Dr. Alice Example <alice@example.com>", "Subject": "Confirm"},
    )
    base.update(kw)
    return Signal(**base)


@pytest.fixture
def captured(monkeypatch):
    """Stub side effects; capture the persisted signal. Force local model + rules only."""
    state = {"persisted": None}

    def _fake_persist(signal):
        state["persisted"] = signal

    def _fake_llm(*a, **k):
        return {"content_summary": "s", "topics": [], "entities": [], "importance_score": 0.5}

    monkeypatch.setattr(sp, "_persist", _fake_persist)
    monkeypatch.setattr(sp, "_call_llm", _fake_llm)
    monkeypatch.setattr(sp, "_load_profile", lambda user_id: None)
    monkeypatch.setattr(sp, "_notify", lambda signal: False)
    monkeypatch.setattr(sp.hooks, "fire_background", lambda *a, **k: None)
    # Email path resolves an on-box model so finalize runs on the real headers.
    import services.email_classifier as ec

    monkeypatch.setattr(ec, "require_local_model", lambda uid, *, task: "llama")
    return state


def test_email_signal_persists_addr_and_display(captured):
    out = sp.process_signal(_signal(), user_id="u1")
    assert out.from_addr == "alice@example.com"  # bare, lowercased
    assert out.from_display == "Dr. Alice Example"  # display name preserved
    assert captured["persisted"].from_addr == "alice@example.com"
    assert captured["persisted"].from_display == "Dr. Alice Example"


def test_email_from_without_display_name(captured):
    out = sp.process_signal(_signal(headers={"From": "no-reply@insurer.com"}), user_id="u1")
    assert out.from_addr == "no-reply@insurer.com"
    assert out.from_display == ""  # no name in the header


def test_email_missing_from_header_is_empty(captured):
    out = sp.process_signal(_signal(headers={"Subject": "Only a subject"}), user_id="u1")
    assert out.from_addr == ""
    assert out.from_display == ""


def test_non_email_signal_does_not_raise_and_has_empty_sender(captured):
    # source_type != "imap" → is_email False → the sender locals are never
    # defined; the ternary must short-circuit (no NameError) and store "".
    rss = _signal(source_type="rss", url="https://example.test/post/1", headers={})
    out = sp.process_signal(rss, user_id="u1")
    assert out.from_addr == ""
    assert out.from_display == ""
    assert out.source_type == "rss"
