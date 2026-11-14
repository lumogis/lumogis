# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-676 — email signals get a deterministic UUID signal_id (never "").

Regression guard for the persistence + Qdrant-collision bug: ``_build_signal``
must produce a valid, non-empty, url-derived UUID so the ``_persist`` Postgres
UUID-PRIMARY-KEY insert succeeds (an empty string fails the cast and is silently
swallowed) and every email gets a distinct Qdrant point id
(``point_id = uuid5("signal::" + signal_id)`` — all "" collapse onto one point).

Runs in the normal suite (no Postgres needed); the live UUID-PK insert is proven
in ``tests/integration/test_email_persist_live.py``.
"""

from __future__ import annotations

import uuid

import pytest
from adapters.email_imap_source import EmailImapSource
from models.signals import SourceConfig


def _src(sid: str = "11111111-1111-1111-1111-111111111111") -> SourceConfig:
    return SourceConfig(
        id=sid,
        name="m",
        source_type="imap",
        url="imap://x",
        category="general",
        active=True,
        poll_interval=3600,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
    )


def _adapter(src: SourceConfig) -> EmailImapSource:
    # Bypass __init__'s credential load — _build_signal only needs self.source.
    adapter = EmailImapSource.__new__(EmailImapSource)
    adapter.source = src
    return adapter


def _point_id(signal_id: str) -> str:
    # Mirrors services.signal_processor._persist's Qdrant point-id derivation.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"signal::{signal_id}"))


def test_build_signal_id_is_valid_uuid_derived_from_url():
    sig = _adapter(_src())._build_signal(uid=5, uidvalidity=100, headers={"Subject": "hi"})
    assert sig.signal_id  # not "" (the bug)
    uuid.UUID(sig.signal_id)  # parses → a valid Postgres UUID PK value
    assert sig.signal_id == str(uuid.uuid5(uuid.NAMESPACE_URL, sig.url))


def test_build_signal_id_stable_across_repolls():
    adapter = _adapter(_src())
    s1 = adapter._build_signal(uid=5, uidvalidity=100, headers={})
    s2 = adapter._build_signal(uid=5, uidvalidity=100, headers={})
    # Same message → same id, so _persist's ON CONFLICT (signal_id) DO NOTHING dedups.
    assert s1.signal_id == s2.signal_id


def test_distinct_messages_get_distinct_qdrant_points():
    adapter = _adapter(_src())
    s1 = adapter._build_signal(uid=5, uidvalidity=100, headers={})
    s2 = adapter._build_signal(uid=6, uidvalidity=100, headers={})
    assert s1.signal_id != s2.signal_id
    # No Qdrant point collision (the second, independent facet of the "" bug).
    assert _point_id(s1.signal_id) != _point_id(s2.signal_id)


def test_empty_signal_id_would_fail_uuid_cast():
    # Documents the pre-fix bug: "" is not a valid UUID, so the Postgres UUID-PK
    # insert in _persist raised and was silently swallowed.
    with pytest.raises(ValueError):
        uuid.UUID("")
