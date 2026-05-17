# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Public regressions for fail-soft GRAPH_MODE wiring (no premium KG imports).

Covers degraded ``service`` / ``inprocess`` paths and synchronization with
:func:`config.set_effective_graph_mode_for_process` per LUM-242.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from unittest.mock import patch

import main

import config


def test_service_mode_import_error_falls_back_disabled(caplog):
    """``patch('services.graph…register_core_callbacks')`` breaks when the submodule
    is missing (mock ``resolve_name`` falls back to ``getattr``, raising AttributeError).
    Prefer ``patch.object`` on a loaded module, or invoke the handler unpatched.
    """
    with caplog.at_level(logging.WARNING, logger="main"):
        try:
            gwd_mod = importlib.import_module(
                "services.graph_webhook_dispatcher",
            )
        except ImportError:
            out = main._wire_graph_mode_handlers("service")
        else:
            with patch.object(
                gwd_mod,
                "register_core_callbacks",
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
