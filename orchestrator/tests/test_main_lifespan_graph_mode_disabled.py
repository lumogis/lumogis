# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Public regressions for fail-soft GRAPH_MODE wiring (no premium KG imports).

Covers degraded ``service`` / ``inprocess`` paths and synchronization with
:func:`config.set_effective_graph_mode_for_process` per LUM-242.
"""

from __future__ import annotations

import importlib.util
import logging
from unittest.mock import patch

import main

import config


def test_service_mode_import_error_falls_back_disabled(caplog):
    with caplog.at_level(logging.WARNING, logger="main"):
        with patch(
            "services.graph_webhook_dispatcher.register_core_callbacks",
            side_effect=ImportError("dispatcher missing"),
        ):
            out = main._wire_graph_mode_handlers("service")
    assert out == "disabled"
    hits = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "graph_mode_fallback"
        and getattr(r, "reason", None) == "service_import_error"
    ]
    assert len(hits) == 1


def test_inprocess_mode_missing_plugin_logs_once(caplog):
    with caplog.at_level(logging.WARNING, logger="main"):
        with patch.object(importlib.util, "find_spec", return_value=None):
            out = main._wire_graph_mode_handlers("inprocess")
    assert out == "disabled"
    hits = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "graph_mode_fallback"
        and getattr(r, "reason", None) == "inprocess_plugin_absent"
    ]
    assert len(hits) == 1


def test_effective_publish_matches_wiring(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    requested = config.read_graph_mode_from_env()
    wired = main._wire_graph_mode_handlers(requested)
    config.set_effective_graph_mode_for_process(wired)
    assert config.get_graph_mode() == "disabled"
