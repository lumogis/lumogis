# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `orchestrator/main.py:_wire_graph_mode_handlers`.

The full `lifespan()` context manager pulls Postgres, Qdrant, Ollama, the
embedder, and the filesystem watcher — none of which are needed to verify
the three GRAPH_MODE branches. The branching logic was extracted into the
module-level `_wire_graph_mode_handlers(graph_mode: str) -> str` helper
specifically so these tests can exercise it in isolation.

For the parts that DO need the full lifespan path (weekly-quality job
gating, dispatcher shutdown), the integration parity test in
`tests/integration/test_graph_parity.py` covers them end-to-end.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import main
import pytest


@pytest.fixture
def patched_handlers():
    """Patch both helpers `_wire_graph_mode_handlers` calls when graph_mode=='service'.

    Returns a 2-tuple of `(register_callbacks_mock, register_proxy_mock)`.
    Both are MagicMock — failing-the-test behaviour is "this was called when
    it shouldn't have been" or the inverse, asserted at the call site.
    """
    with (
        patch("services.graph_webhook_dispatcher.register_core_callbacks") as cb,
        patch("services.kg_premium_core.register_query_graph_proxy") as proxy,
    ):
        yield cb, proxy


def test_wire_graph_mode_inprocess_does_not_call_dispatcher(patched_handlers, caplog):
    cb, proxy = patched_handlers
    with caplog.at_level(logging.INFO, logger="main"):
        result = main._wire_graph_mode_handlers("inprocess")
    assert result == "inprocess"
    cb.assert_not_called()
    proxy.assert_not_called()
    assert any("Graph mode: inprocess" in r.message for r in caplog.records)


def test_wire_graph_mode_service_wires_callbacks_and_proxy(patched_handlers, caplog):
    cb, proxy = patched_handlers
    with caplog.at_level(logging.INFO, logger="main"):
        result = main._wire_graph_mode_handlers("service")
    assert result == "service"
    cb.assert_called_once_with()
    proxy.assert_called_once_with()
    assert any("Graph mode: service" in r.message for r in caplog.records)


def test_wire_graph_mode_disabled_skips_everything(patched_handlers, caplog):
    cb, proxy = patched_handlers
    with caplog.at_level(logging.INFO, logger="main"):
        result = main._wire_graph_mode_handlers("disabled")
    assert result == "disabled"
    cb.assert_not_called()
    proxy.assert_not_called()
    assert any("Graph mode: disabled" in r.message for r in caplog.records)


def test_wire_graph_mode_unknown_returns_disabled_without_dispatcher(patched_handlers, caplog):
    """Defence-in-depth: invalid literals never wire outbound HTTP KG calls."""
    cb, proxy = patched_handlers
    with caplog.at_level(logging.WARNING, logger="main"):
        result = main._wire_graph_mode_handlers("nonsense-mode")
    assert result == "disabled"
    cb.assert_not_called()
    proxy.assert_not_called()
    assert any(
        getattr(r, "event", None) == "graph_mode_fallback"
        and getattr(r, "reason", None) == "invalid_graph_mode_env"
        for r in caplog.records
    )
