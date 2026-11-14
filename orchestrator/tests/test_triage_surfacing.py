# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/triage_surfacing.py (LUM-648).

Copy builders are pure. ``count_needs_you_email`` is exercised here for wiring +
the dedup SQL shape (``COUNT(DISTINCT COALESCE(published_from, signal_id))``); the
real self-share dedup semantics are proven against Postgres in
``tests/integration/test_triage_reminders_live.py``.
"""

from __future__ import annotations

import pytest

from services import triage_surfacing


class _CountStore:
    def __init__(self, n: int | None):
        self.n = n
        self.last_sql = ""
        self.last_params: tuple = ()

    def fetch_one(self, query: str, params: tuple | None = None):
        self.last_sql = " ".join(query.split())
        self.last_params = params or ()
        return {"n": self.n} if self.n is not None else None


@pytest.fixture
def install_store(monkeypatch):
    def _install(n):
        import config as _config

        store = _CountStore(n)
        _config._instances["metadata_store"] = store
        return store

    return _install


def test_needs_you_copy_single_uses_sender_and_subject():
    title, body = triage_surfacing.needs_you_copy(
        {"from_display": "Landlord", "from_addr": "ll@x.com", "title": "Rent due"},
        count=1,
    )
    assert "Landlord" in title
    assert body == "Rent due"


def test_needs_you_copy_single_falls_back_to_addr_then_generic():
    title, body = triage_surfacing.needs_you_copy(
        {"from_display": "", "from_addr": "ll@x.com", "title": "Rent due"}, count=1
    )
    assert "ll@x.com" in title
    title2, body2 = triage_surfacing.needs_you_copy(
        {"from_display": "", "from_addr": "", "title": ""}, count=1
    )
    assert title2 == "An email needs you"
    assert body2 == "(no subject)"


def test_needs_you_copy_grouped():
    title, body = triage_surfacing.needs_you_copy({}, count=3)
    assert body == "3 snoozed emails are back"


def test_brief_copy_singular_and_plural():
    assert triage_surfacing.brief_copy(1) == ("Morning brief", "1 email needs you today")
    assert triage_surfacing.brief_copy(4) == ("Morning brief", "4 emails need you today")


def test_count_needs_you_email_dedups_by_published_from_in_sql(install_store):
    store = install_store(2)
    n = triage_surfacing.count_needs_you_email("alice")
    assert n == 2
    # The dedup key must be COUNT(DISTINCT COALESCE(published_from, signal_id)) —
    # the exact mirror of the frontend collapseProjections key (Sonnet R2 P1).
    assert "count(distinct coalesce(published_from, signal_id))" in store.last_sql.lower()
    # Email-scoped + LATERAL caller-id prepended.
    assert "imap://%" in store.last_params
    assert store.last_params[0] == "alice"


def test_count_needs_you_email_zero_on_error(monkeypatch):
    class _BoomStore:
        def fetch_one(self, *a, **k):
            raise RuntimeError("db down")

    import config as _config

    _config._instances["metadata_store"] = _BoomStore()
    assert triage_surfacing.count_needs_you_email("bob") == 0
