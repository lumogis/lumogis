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
