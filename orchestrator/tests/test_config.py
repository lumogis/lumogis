# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for config.py: factory functions, singleton caching, shutdown."""

import config


def test_get_vector_store_returns_same_instance():
    a = config.get_vector_store()
    b = config.get_vector_store()
    assert a is b


def test_get_metadata_store_returns_same_instance():
    a = config.get_metadata_store()
    b = config.get_metadata_store()
    assert a is b


def test_get_embedder_returns_same_instance():
    a = config.get_embedder()
    b = config.get_embedder()
    assert a is b


def test_shutdown_clears_instances():
    config.get_vector_store()
    config.get_metadata_store()
    config.get_embedder()
    assert len(config._instances) >= 3
    config.shutdown()
    assert len(config._instances) == 0


# ---------------------------------------------------------------------------
# GRAPH_MODE / KG service config helpers
# ---------------------------------------------------------------------------


def test_get_graph_mode_defaults_to_inprocess(monkeypatch):
    monkeypatch.delenv("GRAPH_MODE", raising=False)
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "inprocess"


def test_get_graph_mode_accepts_service(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "service"


def test_get_graph_mode_accepts_disabled(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "disabled"


def test_get_graph_mode_lowercases_and_strips(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "  SERVICE  ")
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "service"


def test_get_graph_mode_unknown_falls_back_to_inprocess(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("GRAPH_MODE", "remote")
    config.get_graph_mode.cache_clear()
    with caplog.at_level(logging.WARNING, logger="config"):
        assert config.get_graph_mode() == "inprocess"
    assert any("not one of" in rec.message for rec in caplog.records)


def test_get_graph_mode_is_cached(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "service"
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    assert config.get_graph_mode() == "service", (
        "value must be cached; only cache_clear() (or shutdown) re-reads env"
    )
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "disabled"


def test_get_kg_service_url_defaults(monkeypatch):
    monkeypatch.delenv("KG_SERVICE_URL", raising=False)
    assert config.get_kg_service_url() == "http://lumogis-graph:8001"


def test_get_kg_service_url_strips_trailing_slashes(monkeypatch):
    monkeypatch.setenv("KG_SERVICE_URL", "http://kg.example.com/api/")
    assert config.get_kg_service_url() == "http://kg.example.com/api"


def test_get_kg_service_url_strips_whitespace(monkeypatch):
    monkeypatch.setenv("KG_SERVICE_URL", "  http://lumogis-graph:8001  ")
    assert config.get_kg_service_url() == "http://lumogis-graph:8001"


def test_get_kg_webhook_secret_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    assert config.get_kg_webhook_secret() is None


def test_get_kg_webhook_secret_returns_none_when_blank(monkeypatch):
    """A blank `GRAPH_WEBHOOK_SECRET=` line in .env must collapse to None
    so it can't be confused for a real (very weak) secret."""
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "   ")
    assert config.get_kg_webhook_secret() is None


def test_get_kg_webhook_secret_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "supersecret123")
    assert config.get_kg_webhook_secret() == "supersecret123"


def test_shutdown_clears_graph_mode_cache(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.get_graph_mode.cache_clear()
    assert config.get_graph_mode() == "service"
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    config.shutdown()  # MUST clear the cache too
    assert config.get_graph_mode() == "inprocess"
