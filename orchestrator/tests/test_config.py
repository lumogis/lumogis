# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for config.py: factory functions, singleton caching, shutdown."""

import pytest

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


def test_get_graph_mode_default_is_disabled(monkeypatch):
    monkeypatch.delenv("GRAPH_MODE", raising=False)
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "disabled"


def test_get_graph_mode_accepts_service(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "service"


def test_get_graph_mode_accepts_disabled(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "disabled"


def test_get_graph_mode_lowercases_and_strips(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "  SERVICE  ")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "service"


def test_get_graph_mode_unknown_values_fallback_disabled(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("GRAPH_MODE", "remote")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    with caplog.at_level(logging.WARNING, logger="config"):
        assert config.get_graph_mode() == "disabled"
    assert any(
        getattr(rec, "event", None) == "graph_mode_fallback"
        and getattr(rec, "reason", None) == "invalid_graph_mode_env"
        for rec in caplog.records
    )


def test_clear_graph_mode_env_cache_rereads_env(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "service"
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    assert config.get_graph_mode() == "service", (
        "value must be cached until clear_graph_mode_env_cache()"
    )
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "disabled"


def test_set_effective_graph_mode_overrides_env_and_clears_cache(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "disabled"
    config.set_effective_graph_mode_for_process("service")
    assert config.get_graph_mode() == "service"
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    assert config.get_graph_mode() == "service"
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "inprocess"


def test_shutdown_clears_effective_override(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    config.set_effective_graph_mode_for_process("service")
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "service"
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    config.shutdown()
    config.clear_graph_mode_env_cache()
    assert config.get_graph_mode() == "disabled"


def test_get_graph_store_falkordb_import_error_returns_none_with_warning(monkeypatch, caplog):
    import logging
    import sys
    import types

    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    config._instances.pop("graph_store", None)
    config._graph_store_import_warning_emitted = False
    fake_mod = types.ModuleType("adapters.falkordb_store")
    monkeypatch.setitem(sys.modules, "adapters.falkordb_store", fake_mod)
    try:
        with caplog.at_level(logging.WARNING, logger="config"):
            assert config.get_graph_store() is None
        hits = [
            rec
            for rec in caplog.records
            if getattr(rec, "event", None) == "graph_store_unavailable"
            and getattr(rec, "reason", None) == "falkordb_adapter_import_error"
        ]
        assert len(hits) == 1
    finally:
        monkeypatch.delitem(sys.modules, "adapters.falkordb_store", raising=False)
        config._instances.pop("graph_store", None)
        config._graph_store_import_warning_emitted = False


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


@pytest.mark.parametrize(
    "graph_secret, insecure_opt_in, expected_require",
    [
        ("mysecret", None, True),
        ("mysecret", "true", True),
        (None, None, True),
        (None, "1", False),
        (None, "true", False),
        (None, "yes", False),
        (None, "on", True),
        (None, "0", True),
        (None, "false", True),
        ("   ", None, True),
    ],
)
def test_config_graph_proxy_require_service_bearer_matrix(
    monkeypatch, graph_secret, insecure_opt_in, expected_require
):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", raising=False)
    if graph_secret is not None:
        monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", graph_secret)
    if insecure_opt_in is not None:
        monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", insecure_opt_in)
    assert config.get_graph_proxy_require_service_bearer() is expected_require
