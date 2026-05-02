# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Boot-time gate tests for ``config._check_background_model_defaults``.

Plan ``llm_provider_keys_per_user_migration`` Pass 2.6.

Asserts:
* Local default + ``AUTH_ENABLED=true``: pass-through (no raise).
* Cloud default + ``AUTH_ENABLED=true``: ``RuntimeError`` whose
  message carries every operator-actionable canary string so a future
  text edit cannot quietly drop the ``boot check guards the *default*
  model only`` clarification (R1 critique D1.1).
* Cloud default + ``AUTH_ENABLED=false``: legacy single-user pass-through.
* Unknown model: WARN log, no raise.
"""

from __future__ import annotations

import logging
import os

import pytest

import config


def test_signal_llm_model_local_under_auth_on_passes_boot(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SIGNAL_LLM_MODEL", "llama")
    config._check_background_model_defaults()


def test_signal_llm_model_cloud_under_auth_on_fails_boot(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SIGNAL_LLM_MODEL", "claude")
    with pytest.raises(RuntimeError) as excinfo:
        config._check_background_model_defaults()
    msg = str(excinfo.value)
    for canary in ("SIGNAL_LLM_MODEL", "claude", "AUTH_ENABLED=true",
                   "boot check guards the *default* model only"):
        assert canary in msg, (
            f"missing canary {canary!r} from boot-check error message; "
            "did the message wording regress?"
        )


def test_signal_llm_model_cloud_under_auth_off_passes_boot(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SIGNAL_LLM_MODEL", "claude")
    config._check_background_model_defaults()


def test_signal_llm_model_unknown_warns_does_not_raise(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SIGNAL_LLM_MODEL", "does_not_exist_anywhere")
    with caplog.at_level(logging.WARNING, logger="config"):
        config._check_background_model_defaults()
    assert any(
        "does_not_exist_anywhere" in r.getMessage() for r in caplog.records
    )
