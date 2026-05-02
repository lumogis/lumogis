# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the import-time GRAPH_MODE guard in `plugins/graph/__init__.py`.

The plugin must NOT register hooks or schedule jobs when `GRAPH_MODE` is anything
other than ``inprocess``. Core never mounts graph HTTP from this plugin (those
routes live in ``lumogis-graph``), so ``router`` is always ``None``.

Without the hook guard, the in-process projection AND the service-side webhook
receiver would both run against the same Postgres + FalkorDB and double-write
``graph_projected_at`` and ``edge_scores`` on every ingest event.

Reload-based test: import-time side effects can only be re-evaluated by
``importlib.reload()``. Each test sets the env var, clears the
``get_graph_mode`` cache, reloads the package, and inspects the result.
"""

from __future__ import annotations

import importlib

import pytest

import config


@pytest.fixture
def reloaded_plugin(monkeypatch):
    """Yield a function that reloads `plugins.graph` after env-var setup.

    The plugin module is import-time evaluated. Reloading is the only way
    to re-run the mode guard against a new env var; resetting hooks state
    afterwards keeps later tests deterministic.
    """
    import hooks
    import plugins.graph as plugin_pkg

    snapshot_listeners = {k: list(v) for k, v in hooks._listeners.items()}

    def _reload():
        config.get_graph_mode.cache_clear()
        return importlib.reload(plugin_pkg)

    yield _reload

    # Restore listeners — reload may have appended new ones.
    hooks._listeners.clear()
    hooks._listeners.update({k: list(v) for k, v in snapshot_listeners.items()})
    config.get_graph_mode.cache_clear()


def test_plugin_router_is_none_in_inprocess(reloaded_plugin, monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    pkg = reloaded_plugin()
    assert pkg.router is None, (
        "Core no longer mounts graph HTTP routers from the plugin; "
        "lumogis-graph owns /graph/backfill and viz HTTP even when projection runs in-process."
    )


def test_plugin_router_is_none_in_service_mode(reloaded_plugin, monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "service")
    pkg = reloaded_plugin()
    assert pkg.router is None, (
        "in service mode the plugin MUST NOT expose a router — "
        "the lumogis-graph service owns those routes; running both would "
        "create routing ambiguity AND double-projection on every ingest"
    )


def test_plugin_router_is_none_in_disabled_mode(reloaded_plugin, monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    pkg = reloaded_plugin()
    assert pkg.router is None


def test_plugin_does_not_register_hooks_in_service_mode(reloaded_plugin, monkeypatch):
    """The smoking-gun test: in service mode, ENTITY_CREATED must have ZERO
    listeners coming from the graph plugin (it self-disables). The
    in-process writer's `on_entity_created` must not appear in
    `hooks._listeners[Event.ENTITY_CREATED]`.
    """
    import hooks
    from events import Event

    hooks._listeners.clear()
    monkeypatch.setenv("GRAPH_MODE", "service")
    reloaded_plugin()

    listener_names = [
        getattr(cb, "__name__", repr(cb)) for cb in hooks._listeners.get(Event.ENTITY_CREATED, [])
    ]
    assert "on_entity_created" not in listener_names, (
        f"plugin registered ENTITY_CREATED listener in service mode: {listener_names}"
    )


def test_plugin_registers_hooks_in_inprocess_mode(reloaded_plugin, monkeypatch):
    """Mirror test: in inprocess mode the writer.on_entity_created MUST be
    registered, otherwise inprocess parity is silently broken."""
    import hooks
    from events import Event

    hooks._listeners.clear()
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    reloaded_plugin()

    listener_names = [
        getattr(cb, "__name__", repr(cb)) for cb in hooks._listeners.get(Event.ENTITY_CREATED, [])
    ]
    assert "on_entity_created" in listener_names, (
        f"plugin failed to register ENTITY_CREATED listener in inprocess mode: {listener_names}"
    )
