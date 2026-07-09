# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for deferred embedding activation."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

from services.embedding_readiness import try_activate_embedding


def test_try_activate_embedding_noop_when_already_ready():
    state = SimpleNamespace(embedding_ready=True)
    assert try_activate_embedding(state) is True


def test_try_activate_embedding_fails_when_ping_false():
    state = SimpleNamespace(embedding_ready=False)
    embedder = MagicMock()
    embedder.ping.return_value = False
    with patch("services.embedding_readiness.config.get_embedder", return_value=embedder):
        assert try_activate_embedding(state) is False
    assert state.embedding_ready is False


def test_try_activate_embedding_sets_ready_on_success():
    state = SimpleNamespace(embedding_ready=False)
    embedder = MagicMock()
    embedder.ping.return_value = True
    embedder.vector_size = 768
    vs = MagicMock()
    with (
        patch("services.embedding_readiness.config.get_embedder", return_value=embedder),
        patch("services.embedding_readiness.config.get_vector_store", return_value=vs),
    ):
        assert try_activate_embedding(state) is True
    assert state.embedding_ready is True
    vs.create_collection.assert_called()
    vs.ensure_payload_index.assert_called()

    index_calls = vs.ensure_payload_index.call_args_list
    # file_path is indexed exactly once, and only on the documents collection.
    file_path_calls = [c for c in index_calls if c.args[1] == "file_path"]
    assert file_path_calls == [call("documents", "file_path")]
    # scope / user_id are still indexed on every embed collection.
    for coll in ("documents", "conversations", "entities", "signals"):
        assert call(coll, "scope") in index_calls
        assert call(coll, "user_id") in index_calls
