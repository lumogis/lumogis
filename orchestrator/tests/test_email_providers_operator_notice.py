# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-758 — does setup tell the member the truth about whether this will work?

LUM-758 makes the proxy apply its own allow file, but two gates can still hold a
new mailbox back, and both are invisible to the person doing the setup:

* the in-process egress **ceiling**, which bakes its allowlist at boot (LUM-636),
* the allow-file **watcher being switched off**, which restores the old
  operator-reload requirement.

The second is the dangerous one. The toggle lives on the proxy service, so if Core
did not read the same variable an operator could disable the watcher while the API
kept reporting that no operator was needed — the same silent non-sync as the
original bug, but now with a UI actively promising otherwise.
"""

from __future__ import annotations

import pytest
from routes.api_v1 import email_providers as route


@pytest.fixture(autouse=True)
def _no_ceiling(monkeypatch):
    monkeypatch.setattr("services.egress_guard.egress_ceiling_enabled", lambda: False)
    monkeypatch.delenv("LUMOGIS_EGRESS_ALLOW_WATCH", raising=False)


def test_no_notice_when_the_watcher_handles_it(monkeypatch):
    assert route._operator_action_reason() is None


def test_ceiling_is_reported(monkeypatch):
    monkeypatch.setattr("services.egress_guard.egress_ceiling_enabled", lambda: True)
    assert route._operator_action_reason() == "ceiling"


def test_disabled_watcher_is_reported(monkeypatch):
    monkeypatch.setenv("LUMOGIS_EGRESS_ALLOW_WATCH", "0")
    assert route._operator_action_reason() == "watch_disabled"


def test_watcher_enabled_by_default_when_unset(monkeypatch):
    # Absent env var must mean "watching", matching the entrypoint's own default.
    # If these two defaults ever disagreed, one side would be lying.
    assert route._operator_action_reason() is None


def test_ceiling_wins_over_watch_flag(monkeypatch):
    # Both gates active: report the one that needs the heavier remedy (a restart).
    monkeypatch.setattr("services.egress_guard.egress_ceiling_enabled", lambda: True)
    monkeypatch.setenv("LUMOGIS_EGRESS_ALLOW_WATCH", "0")
    assert route._operator_action_reason() == "ceiling"
