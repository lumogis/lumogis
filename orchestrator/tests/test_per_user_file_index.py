# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit + call-site contract tests for the per-user point-id helpers.

Covers:
    * services.point_ids — pure-function unit tests (B11 helper math).
    * services.memory.store_session — call-site contract test for the
      `conversations` Qdrant collection (B11 surface #2).
    * adapters.calendar_adapter.CalendarAdapter — call-site contract
      test for the CalDAV signal_id helper (B11 surface #3).

The B12 SQL upsert contract (file_index ON CONFLICT) is exercised in
tests/integration/test_two_user_isolation.py::test_two_users_can_ingest_same_path.
"""

from __future__ import annotations

import types
import uuid

from services.point_ids import caldav_signal_id
from services.point_ids import document_chunk_point_id
from services.point_ids import session_conversation_point_id

# ---------------------------------------------------------------------------
# Helper unit tests (8) — pin the namespace shape.
# ---------------------------------------------------------------------------


def test_document_chunk_point_id_is_user_namespaced():
    a = document_chunk_point_id("alice", "/data/x.pdf", 0)
    b = document_chunk_point_id("bob", "/data/x.pdf", 0)
    assert a != b, "B11 regression: same (file_path, chunk) collides cross-user"
    assert isinstance(a, str) and len(a) == 36
    assert isinstance(b, str) and len(b) == 36


def test_document_chunk_point_id_is_idempotent():
    a1 = document_chunk_point_id("alice", "/data/x.pdf", 0)
    a2 = document_chunk_point_id("alice", "/data/x.pdf", 0)
    assert a1 == a2


def test_document_chunk_point_id_distinguishes_chunks():
    c0 = document_chunk_point_id("alice", "/data/x.pdf", 0)
    c1 = document_chunk_point_id("alice", "/data/x.pdf", 1)
    assert c0 != c1


def test_session_conversation_point_id_is_user_namespaced():
    sid = "11111111-1111-1111-1111-111111111111"
    a = session_conversation_point_id("alice", sid)
    b = session_conversation_point_id("bob", sid)
    assert a != b


def test_session_conversation_point_id_is_idempotent():
    sid = "11111111-1111-1111-1111-111111111111"
    a1 = session_conversation_point_id("alice", sid)
    a2 = session_conversation_point_id("alice", sid)
    assert a1 == a2


def test_caldav_signal_id_is_user_namespaced():
    a = caldav_signal_id("alice", "evt-1")
    b = caldav_signal_id("bob", "evt-1")
    assert a != b


def test_caldav_signal_id_is_idempotent():
    a1 = caldav_signal_id("alice", "evt-1")
    a2 = caldav_signal_id("alice", "evt-1")
    assert a1 == a2


def test_returns_str_not_uuid_object():
    """Qdrant adapter expects str ids; uuid.UUID would silently break it."""
    for value in (
        document_chunk_point_id("alice", "/data/x.pdf", 0),
        session_conversation_point_id("alice", "sid"),
        caldav_signal_id("alice", "evt-1"),
    ):
        assert isinstance(value, str), f"helper returned non-str: {type(value)!r}"
        # str(uuid5(...)) is a 36-char canonical UUID string.
        assert len(value) == 36
        # Round-trips back to a UUID — proves it's a valid UUID string.
        uuid.UUID(value)


# ---------------------------------------------------------------------------
# Test 9 — call-site contract: store_session uses the user-namespaced helper
# for the `conversations` Qdrant point id (B11 surface #2).
# ---------------------------------------------------------------------------


def test_store_session_uses_user_namespaced_point_id(monkeypatch):
    """services.memory.store_session MUST pass a user-namespaced point id
    to vs.upsert(collection="conversations", id=...).

    Locks the second B11 surface: a refactor that drops the user_id arg
    from session_conversation_point_id would compile cleanly and silently
    re-introduce cross-user overwrite for session summaries. The
    documents-side integration test does NOT cover this code path.
    """
    import services.memory as memory_mod
    from models.memory import SessionSummary

    import config as _config

    captured_upserts: list[dict] = []

    class _CapturingVS:
        def upsert(self, *, collection: str, id: str, vector, payload: dict) -> None:
            captured_upserts.append({"collection": collection, "id": id, "payload": payload})

    class _StubEmbedder:
        def embed(self, text: str):
            return [0.0] * 768

    class _NoopMS:
        def execute(self, *_a, **_kw):
            return None

    # Replace the autouse mocks with capturing ones for this test only.
    _config._instances["vector_store"] = _CapturingVS()
    _config._instances["embedder"] = _StubEmbedder()
    _config._instances["metadata_store"] = _NoopMS()

    summary = SessionSummary(
        session_id="11111111-1111-1111-1111-111111111111",
        summary="hi",
        topics=[],
        entities=[],
    )

    memory_mod.store_session(summary, user_id="alice")
    memory_mod.store_session(summary, user_id="bob")

    conversations_upserts = [u for u in captured_upserts if u["collection"] == "conversations"]
    assert len(conversations_upserts) == 2, (
        f"expected 2 `conversations` upserts, got {len(conversations_upserts)}: "
        f"{captured_upserts!r}"
    )

    alice_upsert, bob_upsert = conversations_upserts
    expected_alice = session_conversation_point_id("alice", summary.session_id)
    expected_bob = session_conversation_point_id("bob", summary.session_id)

    assert alice_upsert["id"] == expected_alice, (
        "B11 regression: store_session did NOT use the user-namespaced "
        f"helper for Alice. expected={expected_alice!r} got={alice_upsert['id']!r}"
    )
    assert bob_upsert["id"] == expected_bob, (
        "B11 regression: store_session did NOT use the user-namespaced "
        f"helper for Bob. expected={expected_bob!r} got={bob_upsert['id']!r}"
    )
    assert alice_upsert["id"] != bob_upsert["id"], (
        "B11 regression: same session_id collides cross-user in `conversations`"
    )


# ---------------------------------------------------------------------------
# Test 10 — call-site contract: CalendarAdapter passes self._config.user_id
# to the helper (B11 surface #3, unit-level / mocked-caldav scope).
# ---------------------------------------------------------------------------


def _build_source_config(user_id: str):
    """Construct SourceConfig with the actual fields from models/signals.py."""
    from models.signals import SourceConfig

    return SourceConfig(
        id="cal-test",
        name="cal-test",
        source_type="caldav",
        url="https://example.invalid/cal",
        category="calendar",
        active=True,
        poll_interval=3600,
        extraction_method="caldav",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        user_id=user_id,
    )


def _fake_event(uid_value: str):
    """Minimal fake CalDAV event accepted by CalendarAdapter._event_to_signal.

    `_event_to_signal` only really needs `event.vobject_instance.vevent.uid`;
    every other attribute is read via `getattr(..., None)` with a safe
    fallback. SimpleNamespace returns the default for missing attrs.
    """
    vevent = types.SimpleNamespace(uid=uid_value)
    vobject_instance = types.SimpleNamespace(vevent=vevent)
    return types.SimpleNamespace(vobject_instance=vobject_instance)


def test_caldav_adapter_passes_source_user_id_to_signal_id_helper():
    """CalendarAdapter._event_to_signal MUST namespace signal_id with
    self._config.user_id, not a hardcoded "default" or a missed-rename.

    Unit-level scope: bypasses the live `caldav` library entirely. Live
    CalDAV is an explicit accepted-CI-risk for this chunk (no Radicale
    container in CI; no plan-level commitment to add one).
    """
    from datetime import datetime
    from datetime import timezone

    from adapters.calendar_adapter import CalendarAdapter

    alice_cfg = _build_source_config("alice")
    bob_cfg = _build_source_config("bob")

    adapter_alice = CalendarAdapter(alice_cfg)
    adapter_bob = CalendarAdapter(bob_cfg)

    now = datetime.now(timezone.utc)
    sig_alice = adapter_alice._event_to_signal(_fake_event("evt-1"), now)
    sig_bob = adapter_bob._event_to_signal(_fake_event("evt-1"), now)

    assert sig_alice is not None and sig_bob is not None, (
        "fake event was rejected by _event_to_signal"
    )
    assert sig_alice.signal_id == caldav_signal_id("alice", "evt-1"), (
        "B11 regression: CalDAV adapter did not namespace signal_id with "
        f"self._config.user_id for Alice. got={sig_alice.signal_id!r} "
        f"expected={caldav_signal_id('alice', 'evt-1')!r}"
    )
    assert sig_bob.signal_id == caldav_signal_id("bob", "evt-1"), (
        "B11 regression: CalDAV adapter did not namespace signal_id with "
        f"self._config.user_id for Bob. got={sig_bob.signal_id!r}"
    )
    assert sig_alice.signal_id != sig_bob.signal_id, (
        "B11 regression: same caldav uid collides cross-user"
    )
