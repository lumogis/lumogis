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
        patch("services.tools.register_query_graph_proxy") as proxy,
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


def test_wire_graph_mode_unknown_falls_through_to_inprocess_branch(patched_handlers, caplog):
    """`config.get_graph_mode()` already coerces unknown env values back to
    `"inprocess"` with a WARNING. As a defence-in-depth on the helper itself,
    an unrecognised value passed directly should NOT silently wire up the
    service-mode HTTP dispatcher (that would cause Core to start posting to
    a non-existent KG service on every hook event).
    """
    cb, proxy = patched_handlers
    with caplog.at_level(logging.INFO, logger="main"):
        result = main._wire_graph_mode_handlers("nonsense-mode")
    assert result == "nonsense-mode"
    cb.assert_not_called()
    proxy.assert_not_called()
    # The "else" branch logs the inprocess message even for typos — the
    # config-layer warning is the operator-facing signal.
