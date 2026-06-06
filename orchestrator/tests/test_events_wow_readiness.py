# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SSE wow_readiness_changed fanout (LUM-216 slice 2)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from routes import events as events_mod


@pytest.fixture(autouse=True)
def _clear_wow_timers():
    with events_mod._wow_debounce_lock:
        for timer in events_mod._wow_debounce_timers.values():
            timer.cancel()
        events_mod._wow_debounce_timers.clear()
    yield
    with events_mod._wow_debounce_lock:
        for timer in events_mod._wow_debounce_timers.values():
            timer.cancel()
        events_mod._wow_debounce_timers.clear()


@patch.object(events_mod, "_push_to_connections")
def test_entity_created_schedules_debounced_wow_readiness(mock_push):
    with patch.object(events_mod, "WOW_READINESS_DEBOUNCE_S", 0.05):
        events_mod.on_wow_readiness_entity_created(
            user_id="user-1",
            entity_id="e1",
            name="Alice",
            is_staged=False,
        )
        assert mock_push.call_count == 0
        time.sleep(0.08)
        mock_push.assert_called_once_with("wow_readiness_changed", {}, user_id="user-1")


@patch.object(events_mod, "_push_to_connections")
def test_staged_entity_skips_wow_readiness(mock_push):
    with patch.object(events_mod, "WOW_READINESS_DEBOUNCE_S", 0.05):
        events_mod.on_wow_readiness_entity_created(
            user_id="user-1",
            entity_id="e1",
            name="Alice",
            is_staged=True,
        )
        time.sleep(0.08)
        mock_push.assert_not_called()


@patch.object(events_mod, "_push_to_connections")
def test_document_ingested_debounce_coalesces(mock_push):
    with patch.object(events_mod, "WOW_READINESS_DEBOUNCE_S", 0.1):
        events_mod.on_wow_readiness_document_ingested(user_id="user-1", file_path="/a.md")
        time.sleep(0.05)
        events_mod.on_wow_readiness_document_ingested(user_id="user-1", file_path="/b.md")
        time.sleep(0.05)
        assert mock_push.call_count == 0
        time.sleep(0.1)
        mock_push.assert_called_once_with("wow_readiness_changed", {}, user_id="user-1")
