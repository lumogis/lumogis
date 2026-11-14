# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""CI guards: LUM-586 shared-graph projection requires ``GRAPH_MODE=service`` (LUM-603).

v1 deliberately does **not** port ``project_entity_into_graph`` or
``reconcile_shared_entities`` into the in-process graph plugin. Postgres+Qdrant
shared entity rows are created in any non-``disabled`` graph mode; FalkorDB
projection and reconcile recovery require the out-of-process ``lumogis-graph``
service (webhook consumer).

These tests pin that contract so a future in-process ``on_document_shared``
registration cannot land without an explicit parity decision.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import main
import pytest

import config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GRAPH_PLUGIN = _REPO_ROOT / "orchestrator" / "plugins" / "graph" / "__init__.py"


@pytest.fixture
def reloaded_plugin(monkeypatch):
    """Reload ``plugins.graph`` after env setup (import-time mode guard)."""
    import hooks
    import plugins.graph as plugin_pkg

    snapshot_listeners = {k: list(v) for k, v in hooks._listeners.items()}

    def _reload():
        config.clear_graph_mode_env_cache()
        return importlib.reload(plugin_pkg)

    yield _reload

    hooks._listeners.clear()
    hooks._listeners.update({k: list(v) for k, v in snapshot_listeners.items()})
    config.clear_graph_mode_env_cache()


def test_graph_plugin_source_excludes_on_document_shared():
    """The in-process plugin must never register ``on_document_shared`` (LUM-603)."""
    text = _GRAPH_PLUGIN.read_text(encoding="utf-8")
    assert "on_document_shared" not in text, (
        "plugins/graph must not wire on_document_shared in-process — "
        "shared FalkorDB projection is service-mode-only (LUM-586/603)"
    )


@pytest.mark.parametrize("mode", ["inprocess", "service"])
def test_graph_plugin_never_registers_on_document_shared(reloaded_plugin, monkeypatch, mode):
    import hooks
    from events import Event

    hooks._listeners.clear()
    monkeypatch.setenv("GRAPH_MODE", mode)
    reloaded_plugin()

    listener_names = [
        getattr(cb, "__name__", repr(cb)) for cb in hooks._listeners.get(Event.DOCUMENT_SHARED, [])
    ]
    assert "on_document_shared" not in listener_names, (
        f"plugin registered on_document_shared in GRAPH_MODE={mode}: {listener_names}"
    )


def test_lifespan_inprocess_does_not_register_webhook_dispatcher():
    """Core lifespan must not wire KG webhooks when ``GRAPH_MODE=inprocess``."""
    from unittest.mock import patch

    with patch("services.graph_webhook_dispatcher.register_core_callbacks") as cb:
        result = main._wire_graph_mode_handlers("inprocess")
    assert result == "inprocess"
    cb.assert_not_called()


def test_document_shared_webhook_consumer_is_dispatcher_only():
    """``DOCUMENT_SHARED`` is consumed only via ``register_core_callbacks`` (service)."""
    import hooks
    import services.graph_webhook_dispatcher as dispatcher
    from events import Event

    dispatcher._reset_for_tests()
    hooks._listeners.pop(Event.DOCUMENT_SHARED, None)

    dispatcher.register_core_callbacks()

    listeners = hooks._listeners.get(Event.DOCUMENT_SHARED, [])
    assert len(listeners) == 1
    assert listeners[0].__name__ == "webhook_dispatch_on_document_shared"


def test_cascade_still_projects_rows_in_inprocess_without_graph_consumer(monkeypatch):
    """``inprocess`` creates PG rows + fires the hook; graph projection stays external."""
    import hooks
    from auth import UserContext
    from events import Event

    from services import document_entity_cascade as cascade
    from services import projection as projection_mod

    fired: list = []
    monkeypatch.setattr(config, "get_graph_mode", lambda: "inprocess")
    monkeypatch.setattr(hooks, "fire_background", lambda ev, **k: fired.append((ev, k)))

    class _MS:
        def fetch_all(self, *_a, **_k):
            return [{"entity_id": "e1", "name": "Ada", "entity_type": "PERSON"}]

    monkeypatch.setattr(config, "get_metadata_store", lambda: _MS())
    monkeypatch.setattr(
        projection_mod,
        "project_entity",
        lambda *a, **k: {"entity_id": "p1"},
    )

    actor = UserContext(user_id="alice", is_authenticated=True)
    projected, failed = cascade.cascade_share_document_entities(
        src_file={"file_path": "/d.md", "user_id": "alice"}, actor=actor
    )
    assert (projected, failed) == (1, 0)
    assert fired and fired[0][0] == Event.DOCUMENT_SHARED
    # No in-process graph writer — operator must run GRAPH_MODE=service for FalkorDB.
